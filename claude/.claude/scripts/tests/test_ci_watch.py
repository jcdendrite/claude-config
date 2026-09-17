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
import re
import shlex
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import assert_cap_engaged, scaled_shim_sleep, write_scaled_timeout_shim

from .conftest import (
    _base_test_env,
    _direnv_shim_source_exits_nonzero_with_unset_payload,
    _direnv_shim_source_reads_stdin,
    _direnv_shim_source_stalls_without_reading_stdin,
    _direnv_shim_source_static_export,
    _direnv_shim_source_unconditional_unset,
    _shimmed_env,
    require_direnv,
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
    repo_host_log=None,
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
    repo_host_log -> optional path; when given, the shim appends one
                      tab-separated line per matched call recording that
                      call's own GH_REPO and GH_HOST.
                      Each field is "<unset>" when the var is absent from
                      that call's environment, distinct from an empty-string
                      present value.
                      Lets a test assert that ci-watch.sh's blanket
                      `unset GH_REPO GH_HOST` actually reaches every gh
                      invocation.
    """
    token_log_repr = repr(str(token_log)) if token_log is not None else "None"
    repo_host_log_repr = repr(str(repo_host_log)) if repo_host_log is not None else "None"
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
        REPO_HOST_LOG = {repo_host_log_repr}

        def log_call(name):
            if TOKEN_LOG is not None:
                with open(TOKEN_LOG, "a") as f:
                    f.write(
                        name + "\\t"
                        + os.environ.get("GH_TOKEN", "") + "\\t"
                        + os.environ.get("GH_ENTERPRISE_TOKEN", "") + "\\n"
                    )
            if REPO_HOST_LOG is not None:
                with open(REPO_HOST_LOG, "a") as f:
                    f.write(
                        name + "\\t"
                        + os.environ.get("GH_REPO", "<unset>") + "\\t"
                        + os.environ.get("GH_HOST", "<unset>") + "\\n"
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

    Routes through the same `_shimmed_env` PATH seam
    `cleanup-merged-branches.sh`'s tests use, since `resolve_ci_checks_gh_token`
    shells out to real `direnv`; without this seam a test would invoke
    whatever `direnv`/`.envrc` is actually on the running machine.

    direnv_source/direnv_present pass straight through to _shimmed_env, for
    tests exercising resolve_ci_checks_gh_token's direnv resolution path
    instead of the plain ambient-env case every other test here covers.
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
            # Layers CI_CHECKS_GH_TOKEN/GH_TOKEN fixtures on top of the
            # credential-scrubbed base env, modeling a container that has
            # (or hasn't) exported the classic-PAT override — never sourced
            # from the calling test process's own os.environ, which
            # _base_test_env() has already stripped of every
            # _SENSITIVE_ENV_VARS entry.
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


def _parse_repo_host_log(path: Path) -> dict[str, tuple[str, str]]:
    """Map call name ("view"/"watch"/"json") -> (GH_REPO, GH_HOST) as
    recorded by the gh shim's log_call, one entry per matched invocation.
    Each field is "<unset>" when that var was absent from the call's own
    environment, distinct from an empty-string present value."""
    calls = {}
    for line in path.read_text().splitlines():
        name, gh_repo, gh_host = line.split("\t")
        calls[name] = (gh_repo, gh_host)
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


def test_gh_repo_and_host_unset_in_ambient_env_leaves_calls_unaffected(fake_gh, tmp_path):
    # No regression from the blanket `unset GH_REPO GH_HOST`: with neither
    # var set in the ambient env to begin with, every gh call still sees
    # them genuinely absent, matching today's behavior.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["view"] == ("<unset>", "<unset>")
    assert calls["watch"] == ("<unset>", "<unset>")
    assert calls["json"] == ("<unset>", "<unset>")


def test_ambient_gh_repo_and_host_are_unset_before_every_gh_call(fake_gh, tmp_path):
    # GH_REPO/GH_HOST override gh's cwd-based repo/host resolution (gh help
    # environment) -- a stale value inherited from a differently-scoped
    # invoking shell must never reach any of this script's three gh calls,
    # since none of them pass --repo.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        extra_env={
            "GH_REPO": "stale-org/stale-repo",
            "GH_HOST": "stale.ghe.com",
        },
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["view"] == ("<unset>", "<unset>")
    assert calls["watch"] == ("<unset>", "<unset>")
    assert calls["json"] == ("<unset>", "<unset>")


# ---------------------------------------------------------------------------
# direnv_probe — unit-level coverage of the helper itself
#
# resolve_ci_checks_gh_token and resolve_gh_host both build on direnv_probe.
# These tests drive it directly, with a stubbed direnv_export_bash and no
# gh/direnv shim binaries on PATH, instead of going through a full
# ci-watch.sh subprocess run -- the full-script tests throughout this file
# already cover resolve_ci_checks_gh_token/resolve_gh_host's own wiring of
# direnv_probe's two modes into the cross-host mismatch gate.
# ---------------------------------------------------------------------------

def _extract_direnv_probe_source() -> str:
    """Return ci-watch.sh's direnv_probe function definition, verbatim.

    Located by its own `direnv_probe() {` / closing `}` (at column 0)
    boundaries, not by markers -- direnv_probe is a small, self-contained
    function with no top-level side effects of its own, unlike the rest of
    ci-watch.sh (arg validation, resolve_ci_checks_gh_token, the gh calls),
    so sourcing just this slice runs none of that.
    """
    script_text = _SCRIPT.read_text()
    match = re.search(r"^direnv_probe\(\) \{\n.*?^\}\n", script_text, re.DOTALL | re.MULTILINE)
    assert match is not None, "direnv_probe() function not found in ci-watch.sh"
    extracted = match.group(0)
    assert 'eval "$(direnv_export_bash)"' in extracted, (
        f"extracted block is missing its direnv_export_bash call; the "
        f"{{...}} boundary match in ci-watch.sh probably grabbed the wrong "
        f"span. Got: {extracted!r}"
    )
    return extracted


def _run_direnv_probe(
    *, direnv_export_bash_source: str, var_name: str, mode: str, extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run direnv_probe(var_name, mode) in isolation.

    direnv_export_bash_source is a complete `direnv_export_bash() { ... }`
    definition standing in for _direnv-lib.sh's real one -- direnv_probe
    only ever calls it by name, so a stub drives every branch without a
    real direnv binary. extra_env seeds the ambient environment (e.g. a
    pre-existing value for var_name) before direnv_probe runs.
    """
    script = "\n".join([
        "set -euo pipefail",
        direnv_export_bash_source,
        _extract_direnv_probe_source(),
        'direnv_probe "$1" "$2"',
    ])
    env = {**_base_test_env(), **(extra_env or {})}
    return subprocess.run(
        ["bash", "-c", script, "bash", var_name, mode],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def test_direnv_probe_ambient_mode_reports_value_stub_sets():
    result = _run_direnv_probe(
        direnv_export_bash_source='direnv_export_bash() { printf "export DIRENV_PROBE_TEST_VAR=direnv-value\\n"; }',
        var_name="DIRENV_PROBE_TEST_VAR",
        mode="ambient",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1direnv-value"


def test_direnv_probe_ambient_mode_reports_zero_when_stub_leaves_variable_unset():
    result = _run_direnv_probe(
        direnv_export_bash_source="direnv_export_bash() { :; }",
        var_name="DIRENV_PROBE_TEST_VAR",
        mode="ambient",
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "0"


def test_direnv_probe_unset_first_mode_forces_unset_before_eval():
    # Proves unset-first actually unsets var_name before invoking
    # direnv_export_bash, rather than merely reading its pre-existing
    # ambient value -- the stub here never re-exports it, so a "1..."
    # result would mean the forced unset didn't take effect.
    result = _run_direnv_probe(
        direnv_export_bash_source="direnv_export_bash() { :; }",
        var_name="DIRENV_PROBE_TEST_VAR",
        mode="unset-first",
        extra_env={"DIRENV_PROBE_TEST_VAR": "preexisting-ambient-value"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "0"


def test_direnv_probe_unset_first_mode_reports_value_stub_reexports():
    result = _run_direnv_probe(
        direnv_export_bash_source='direnv_export_bash() { printf "export DIRENV_PROBE_TEST_VAR=fresh-value\\n"; }',
        var_name="DIRENV_PROBE_TEST_VAR",
        mode="unset-first",
        extra_env={"DIRENV_PROBE_TEST_VAR": "preexisting-ambient-value"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == "1fresh-value"


def test_direnv_probe_subshell_contains_unrelated_stub_exports():
    # direnv_probe's own comment (ci-watch.sh) documents that only
    # var_name's own presence and value may escape its subshell, via
    # stdout -- an unrelated variable the stub also exports must not reach
    # the caller's environment.
    stub_exports = (
        'export DIRENV_PROBE_TEST_VAR=direnv-value\\n'
        'export DIRENV_PROBE_UNRELATED_VAR=leaked-secret\\n'
    )
    script = "\n".join([
        "set -euo pipefail",
        f'direnv_export_bash() {{ printf "{stub_exports}"; }}',
        _extract_direnv_probe_source(),
        'probed=$(direnv_probe DIRENV_PROBE_TEST_VAR ambient)',
        'printf "%s\\n" "$probed"',
        'printf "DIRENV_PROBE_UNRELATED_VAR:%s\\n" "${DIRENV_PROBE_UNRELATED_VAR:-<unset>}"',
    ])
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=_base_test_env(),
        check=False,
    )
    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "1direnv-value"
    assert lines[1] == "DIRENV_PROBE_UNRELATED_VAR:<unset>"


# ---------------------------------------------------------------------------
# resolve_ci_checks_gh_token — direnv resolution of CI_CHECKS_GH_TOKEN
#
# ci-watch.sh is launched via `Bash` `run_in_background`, a non-interactive
# shell that never fires direnv's PROMPT_COMMAND hook. resolve_ci_checks_gh_token
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


def test_direnv_present_with_nothing_to_say_suppresses_notice(fake_gh, tmp_path):
    # direnv installed, CI_CHECKS_GH_TOKEN absent from the ambient env, and
    # this directory's .envrc has nothing to say about it (the default
    # no-op shim) — the common case on any contributor machine with direnv
    # installed but no CI_CHECKS_GH_TOKEN provisioning. Must not spam the
    # "resolved via direnv" notice on every such run.
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
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
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
    # The other half of the 2x2: CI_CHECKS_GH_TOKEN resolved via direnv but
    # GH_HOST did not, so the cross-host mismatch gate's own
    # GH_HOST_RESOLVED_VIA_DIRENV condition is never satisfied.
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


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


def test_direnv_supplied_token_value_containing_delimiters_round_trips(fake_gh, tmp_path):
    # Falsifies a future accidental reintroduction of text/regex parsing of
    # the export payload in place of `${VAR+x}` presence detection: a value
    # containing `=` and `;` must round-trip exactly.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    value = "token=with=equals;and;semicolons"
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        direnv_source=_direnv_shim_source_static_export("CI_CHECKS_GH_TOKEN", value),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == (value, "")
    assert calls["json"] == (value, "")
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr


def test_direnv_resolving_token_to_empty_string_over_nonempty_ambient_clears_it(fake_gh, tmp_path):
    # value_probe's true branch unconditionally assigns
    # CI_CHECKS_GH_TOKEN="${value_probe:1}" with no non-empty guard (unlike
    # GH_HOST's `-n` guard below) -- an explicit `.envrc` export of
    # CI_CHECKS_GH_TOKEN="" leaves CI_CHECKS_GH_TOKEN present-but-empty.
    token_log = tmp_path / "token.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-token-to-be-cleared"},
        direnv_source=_direnv_shim_source_multi_export({
            "CI_CHECKS_GH_TOKEN": "",
            "GH_HOST": "octocat.ghe.com",
        }),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    # (a) the per-token notice fires, since the resolved value ("") differs
    # from the non-empty ambient value.
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr
    # (b) gh_with_checks_token's own -n guard treats the now-empty
    # CI_CHECKS_GH_TOKEN as unset, so both wrapped calls fall back to
    # unwrapped gh.
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "using CI_CHECKS_GH_TOKEN override" not in result.stderr
    # (c) the cross-host mismatch gate stays silent even with GH_HOST also
    # resolving to a *.ghe.com host in the same run -- its own -n
    # "${CI_CHECKS_GH_TOKEN:-}" guard protects this case regardless of
    # whether CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV also gates it off.
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_direnv_explicit_unset_statement_clears_ambient_token(fake_gh, tmp_path):
    # This is a real-direnv-reachable scenario, not just a synthetic shim
    # input: direnv emits an explicit `unset NAME` when a prior directory's
    # .envrc exported the variable and this directory's does not (see
    # test_real_direnv_cd_between_directories_clears_stale_token below for
    # the real-direnv end-to-end version of this exact transition).
    # value_probe evaluates against the ambient environment, so this
    # payload's `unset` genuinely clears the ambient token.
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
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "ci-watch: CI_CHECKS_GH_TOKEN cleared by direnv for" in result.stderr


def test_direnv_exiting_nonzero_leaves_ambient_untouched_and_does_not_abort(fake_gh, tmp_path):
    # Models a non-`allow`ed .envrc, where `export bash` exits 1 but still
    # writes an unset payload to stdout. resolve_ci_checks_gh_token's guard
    # must discard this cleanly rather than aborting under set -euo pipefail,
    # since a multi-hour background watch must never die to a misbehaving
    # .envrc.
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
    # A discarded direnv failure looks identical to direnv having nothing to
    # say, since both leave the resolved value equal to ambient. The notice
    # is therefore suppressed either way.
    assert "resolved via direnv" not in result.stderr


def _direnv_shim_source_exits_zero_with_unparseable_payload(name: str) -> str:
    """direnv shim where `export bash` exits 0 but the payload's second line
    is syntactically invalid. The first line's export runs and corrupts the
    value before the second line's failure aborts `eval`. Only
    `resolve_ci_checks_gh_token`'s own `status=$?` guard catches this —
    `direnv_export_bash`'s internal `|| return 0` guard only catches a
    nonzero exit from `direnv` itself, which never happens here."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("export {name}=corrupted-partial-value")
            print("export OTHER_VAR='unterminated")
        sys.exit(0)
    """)


def _direnv_shim_source_fails_first_call_then_static_exports(counter_file: str, exports: dict) -> str:
    """direnv shim whose first `export bash` call fails via the same
    syntactically-invalid-payload technique as
    _direnv_shim_source_exits_zero_with_unparseable_payload above, and every
    later call exports `exports` unconditionally. Models a .envrc that fails
    transiently on one probe (resolve_ci_checks_gh_token's own value_probe)
    but answers normally on a later probe against the same directory
    (named_probe, then resolve_gh_host's own probe) — the fail-open-hole
    CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK guards against. counter_file persists
    the call count across the separate `direnv` subprocess invocations."""
    payload = json.dumps(exports)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import shlex
        import sys
        from pathlib import Path

        COUNTER_FILE = Path({counter_file!r})
        EXPORTS = json.loads({payload!r})

        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            count = int(COUNTER_FILE.read_text()) if COUNTER_FILE.exists() else 0
            count += 1
            COUNTER_FILE.write_text(str(count))
            if count == 1:
                print("export OTHER_VAR='unterminated")
            else:
                for name, value in EXPORTS.items():
                    print(f"export {{name}}={{shlex.quote(value)}}")
        sys.exit(0)
    """)


def test_direnv_export_succeeding_with_unparseable_payload_leaves_ambient_untouched_and_does_not_abort(fake_gh, tmp_path):
    # Unlike test_direnv_exiting_nonzero_leaves_ambient_untouched_and_does_not_abort
    # above, where direnv itself exits nonzero, this shim's `export bash`
    # exits 0 and the failure only surfaces partway through `eval`.
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
        direnv_source=_direnv_shim_source_exits_zero_with_unparseable_payload(
            name="CI_CHECKS_GH_TOKEN",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("ambient-survives-token", "")
    assert calls["json"] == ("ambient-survives-token", "")
    assert "corrupted-partial-value" not in result.stdout
    assert "corrupted-partial-value" not in result.stderr
    assert "resolved via direnv" not in result.stderr


def test_direnv_export_containing_other_vars_does_not_perturb_running_script(fake_gh, tmp_path):
    # Subshell-containment guarantee: an .envrc exporting GH_TOKEN, PATH, and
    # STDERR_FILE alongside CI_CHECKS_GH_TOKEN must not let any of the first
    # three cross into the running script's own environment — only
    # CI_CHECKS_GH_TOKEN's resolved value may. Two tells prove containment held:
    # - The unwrapped `gh pr view` call never receives the CI_CHECKS_GH_TOKEN
    #   override, so it only ever sees a leaked GH_TOKEN if containment failed.
    # - STDERR_FILE: if containment failed and STDERR_FILE were hijacked to
    #   this decoy path, the trap's `rm -f "$STDERR_FILE"` would delete it.
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


@pytest.mark.timing
def test_direnv_export_wall_clock_cap_interrupts_stalled_envrc(fake_gh, tmp_path):
    # direnv_export_bash caps `direnv export bash` at 5s (_direnv-lib.sh) —
    # a stalled .envrc must not wedge ci-watch.sh's unattended background
    # run indefinitely. The shim sleeps well past the cap without reading
    # stdin, so the scaled timeout(1) shim's own started/completed record
    # proves the wall-clock cap fired, rather than the </dev/null guard
    # test_stdin_reading_envrc_does_not_hang already covers. The stalled
    # shim is hit (and capped) three times: resolve_ci_checks_gh_token's
    # own value_probe and named_probe each call direnv_export_bash once,
    # and resolve_gh_host calls it once more.
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-survives-token"},
        direnv_source=_direnv_shim_source_stalls_without_reading_stdin(scaled_shim_sleep(30)),
    )
    shim_dir = Path(env["PATH"].split(os.pathsep)[0])
    write_scaled_timeout_shim(shim_dir)
    with assert_cap_engaged(shim_dir, production_cap=5, killed_calls=3):
        result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    # A timed-out direnv resolution must degrade to the existing "ambient
    # value untouched" fallback, not just avoid crashing.
    calls = _parse_token_log(token_log)
    assert calls["watch"] == ("ambient-survives-token", "")
    assert calls["json"] == ("ambient-survives-token", "")
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert repo_host_calls["watch"] == ("<unset>", "<unset>")
    assert repo_host_calls["json"] == ("<unset>", "<unset>")
    assert "resolved via direnv" not in result.stderr


# ---------------------------------------------------------------------------
# require_direnv — unit-level coverage of the helper itself
#
# The two real-direnv end-to-end tests below both gate on require_direnv(),
# but neither one can exercise its hard-fail branch (GITHUB_ACTIONS set,
# direnv absent) — that combination never occurs on a real CI runner once
# .github/workflows/tests.yml's "Install stow and direnv" step has run.
# These tests drive require_direnv() directly, stubbing shutil.which so
# neither branch depends on whether direnv actually happens to be
# installed on the machine running the suite.
# ---------------------------------------------------------------------------

def test_require_direnv_raises_when_github_actions_set_and_direnv_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    with pytest.raises(pytest.fail.Exception):
        require_direnv()


def test_require_direnv_skips_when_github_actions_unset_and_direnv_absent(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    with pytest.raises(pytest.skip.Exception):
        require_direnv()


def test_resolve_ci_checks_gh_token_against_real_direnv_end_to_end(tmp_path):
    # Only test in this file running real direnv end-to-end, to catch drift
    # between the shims above and real direnv's own export behavior.
    # `XDG_DATA_HOME`/`DIRENV_CONFIG` are redirected into `tmp_path` so this
    # test never touches this machine's real direnv allow-store or config.
    # Runs in CI too: .github/workflows/tests.yml's "Install stow and
    # direnv" step installs whatever direnv version Ubuntu 24.04's apt
    # repo carries.
    require_direnv()
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    # Token reconfirms at its already-ambient value, so the per-token
    # notice stays silent — but the presence-based flag still latches.
    (repo_dir / ".envrc").write_text(
        "export CI_CHECKS_GH_TOKEN=ambient-reconfirmed-token\n"
        "export GH_HOST=octocat.ghe.com\n"
    )

    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    gh_shim_dir = tmp_path / "gh_shim"
    gh_shim_dir.mkdir()
    gh_shim = gh_shim_dir / "gh"
    gh_shim.write_text(_gh_shim_source(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
    ))
    gh_shim.chmod(0o755)

    env = {
        **_base_test_env(),
        "PATH": os.pathsep.join([str(gh_shim_dir), os.environ.get("PATH", "")]),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "DIRENV_CONFIG": str(tmp_path / "direnv-config"),
        "CI_CHECKS_GH_TOKEN": "ambient-reconfirmed-token",
    }
    allow_result = subprocess.run(
        ["direnv", "allow", "."], cwd=repo_dir, env=env,
        capture_output=True, text=True, check=False,
    )
    assert allow_result.returncode == 0, allow_result.stderr

    result = subprocess.run(
        [str(_SCRIPT), _PR_NUMBER], cwd=repo_dir, env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert calls["watch"] == ("ambient-reconfirmed-token", "")
    assert calls["json"] == ("ambient-reconfirmed-token", "")
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert "ambient-reconfirmed-token" not in result.stdout
    assert "ambient-reconfirmed-token" not in result.stderr
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" not in result.stderr
    # The crux: if CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV were value-equality
    # gated instead of presence-based, this would incorrectly fire.
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_real_direnv_cd_between_directories_clears_stale_token(tmp_path):
    # The load-bearing regression test for the bug this fix closes: a
    # contributor cd's from a directory whose .envrc exported
    # CI_CHECKS_GH_TOKEN into a sibling directory with no .envrc at all.
    # Priming this process from dir_a first (eval'ing its own `direnv
    # export bash`) populates direnv's own DIRENV_DIFF bookkeeping, the
    # same way an interactive shell's `cd` hook would. This process starts
    # with CI_CHECKS_GH_TOKEN genuinely absent, so direnv's own diff
    # records it as a variable direnv itself added in dir_a — direnv only
    # unsets a variable its own diff shows it added, not one that merely
    # happened to already equal the .envrc's exported value beforehand.
    # dir_b's own `direnv export bash` payload then contains an explicit
    # `unset CI_CHECKS_GH_TOKEN;`. Runs in CI too, like
    # test_resolve_ci_checks_gh_token_against_real_direnv_end_to_end above:
    # .github/workflows/tests.yml's "Install stow and direnv" step installs
    # direnv there as well.
    require_direnv()
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    (dir_a / ".envrc").write_text("export CI_CHECKS_GH_TOKEN=dir-a-scoped-token\n")

    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    gh_shim_dir = tmp_path / "gh_shim"
    gh_shim_dir.mkdir()
    gh_shim = gh_shim_dir / "gh"
    gh_shim.write_text(_gh_shim_source(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
    ))
    gh_shim.chmod(0o755)

    env = {
        **_base_test_env(),
        "PATH": os.pathsep.join([str(gh_shim_dir), os.environ.get("PATH", "")]),
        "XDG_DATA_HOME": str(tmp_path / "xdg-data"),
        "DIRENV_CONFIG": str(tmp_path / "direnv-config"),
    }
    # CI_CHECKS_GH_TOKEN is absent from env here (scrubbed by
    # _base_test_env), so the priming step below is the only source of the
    # ambient value the running script will see.
    assert "CI_CHECKS_GH_TOKEN" not in env
    allow_result = subprocess.run(
        ["direnv", "allow", "."], cwd=dir_a, env=env,
        capture_output=True, text=True, check=False,
    )
    assert allow_result.returncode == 0, allow_result.stderr

    # One bash process: cd into dir_a and eval its direnv export (priming
    # DIRENV_DIFF the way an interactive shell's `cd` hook would), then cd
    # into dir_b (no .envrc) and exec the script — reproducing the real
    # interactive-shell sequence inside one process's environment.
    prime_and_run = (
        f"cd {shlex.quote(str(dir_a))} && eval \"$(direnv export bash)\" "
        f"&& cd {shlex.quote(str(dir_b))} "
        f"&& exec {shlex.quote(str(_SCRIPT))} {shlex.quote(_PR_NUMBER)}"
    )
    result = subprocess.run(
        ["bash", "-c", prime_and_run], env=env,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    # The bug this fix closes: the stale dir_a-scoped token must not reach
    # dir_b's gh calls.
    assert calls["watch"] == ("", "")
    assert calls["json"] == ("", "")
    assert "dir-a-scoped-token" not in result.stdout
    assert "dir-a-scoped-token" not in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN cleared by direnv for" in result.stderr
    # GH_HOST is exempt from this bug class: the script's own top-level
    # `unset GH_REPO GH_HOST` already puts it in the state
    # CI_CHECKS_GH_TOKEN's named_probe has to construct itself.
    assert repo_host_calls["watch"] == ("<unset>", "<unset>")


# ---------------------------------------------------------------------------
# resolve_gh_host — direnv resolution of GH_HOST
#
# Same staleness problem as resolve_ci_checks_gh_token above, but for
# GH_HOST — see docs/scripts.md's GH_HOST entry for the full mechanism.
# ---------------------------------------------------------------------------

def test_direnv_supplies_gh_host_when_ambient_unset(fake_gh, tmp_path):
    # test_ambient_gh_repo_and_host_are_unset_before_every_gh_call (above)
    # pins the stale-value-neutralization half. This test pins the resync
    # half: this directory's own direnv-supplied GH_HOST must reach every
    # gh call.
    # GH_REPO stays unset throughout: resolve_gh_host has no GH_REPO
    # counterpart.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["view"] == ("<unset>", "octocat.ghe.com")
    assert calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert calls["json"] == ("<unset>", "octocat.ghe.com")
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr


def test_direnv_supplied_gh_host_value_containing_delimiters_round_trips(fake_gh, tmp_path):
    # Mirrors test_direnv_supplied_token_value_containing_delimiters_round_trips
    # above, for GH_HOST's own `${VAR+x}` presence detection.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    value = "a;b.ghe.com"
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_static_export("GH_HOST", value),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["watch"] == ("<unset>", value)
    assert calls["json"] == ("<unset>", value)
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr


def test_direnv_resolving_gh_host_to_empty_string_leaves_it_unset(fake_gh, tmp_path):
    # No prior test supplies an explicit empty-string GH_HOST export. This is
    # a notice-accuracy case, not a security-relevant one. The cross-host
    # mismatch warning already can't false-fire here: it gates on `GH_HOST`
    # matching `*.ghe.com`, and `GH_HOST` ends up genuinely absent below.
    # The notice itself stays silent too, since the resolved value ("")
    # equals `AMBIENT_GH_HOST`'s own default-unset value ("").
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_static_export("GH_HOST", ""),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    # resolve_gh_host's `-n "$resolved"` guard leaves an empty direnv-
    # resolved value unexported, so GH_HOST stays genuinely absent from
    # every gh call rather than being exported empty.
    assert calls["view"] == ("<unset>", "<unset>")
    assert calls["watch"] == ("<unset>", "<unset>")
    assert calls["json"] == ("<unset>", "<unset>")
    assert "resolved via direnv" not in result.stderr


def test_direnv_resolving_gh_host_to_same_ambient_value_suppresses_notice(fake_gh, tmp_path):
    # Mirrors test_direnv_resolving_to_same_ambient_value_suppresses_notice
    # above, for GH_HOST: direnv confirming the same value already ambient
    # before the script's own blanket `unset GH_REPO GH_HOST` must not fire
    # the notice, or every run on a contributor machine with a stable
    # GH_HOST .envrc would spam this line even though nothing changed.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        extra_env={"GH_HOST": "octocat.ghe.com"},
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert calls["json"] == ("<unset>", "octocat.ghe.com")
    assert "resolved via direnv" not in result.stderr


def test_direnv_exiting_nonzero_for_gh_host_leaves_it_absent_and_does_not_abort(fake_gh, tmp_path):
    # Mirrors test_direnv_exiting_nonzero_leaves_ambient_untouched_and_does_not_abort
    # above, for GH_HOST: an un-`allow`ed .envrc must not abort the script,
    # and GH_HOST must end up genuinely absent (not exported empty) from
    # every gh call.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_exits_nonzero_with_unset_payload(
            name="GH_HOST",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["view"] == ("<unset>", "<unset>")
    assert calls["watch"] == ("<unset>", "<unset>")
    assert calls["json"] == ("<unset>", "<unset>")
    assert "resolved via direnv" not in result.stderr


def test_direnv_export_succeeding_with_unparseable_gh_host_payload_leaves_ambient_untouched_and_does_not_abort(fake_gh, tmp_path):
    # Mirrors `test_direnv_export_succeeding_with_unparseable_payload_leaves_ambient_untouched_and_does_not_abort`
    # above, for `resolve_gh_host`'s identical `status=$?` guard. `export
    # bash` exits 0 but the payload's second line is syntactically invalid,
    # so the first line's export runs and corrupts `GH_HOST` before eval
    # aborts.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        extra_env={"GH_HOST": "ambient-survives.ghe.com"},
        direnv_source=_direnv_shim_source_exits_zero_with_unparseable_payload(
            name="GH_HOST",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    calls = _parse_repo_host_log(repo_host_log)
    assert calls["view"] == ("<unset>", "<unset>")
    assert calls["watch"] == ("<unset>", "<unset>")
    assert calls["json"] == ("<unset>", "<unset>")
    assert "corrupted-partial-value" not in result.stdout
    assert "corrupted-partial-value" not in result.stderr
    assert "resolved via direnv" not in result.stderr


def test_gh_host_resolved_via_direnv_pairs_with_ambient_ci_checks_token(fake_gh, tmp_path):
    # Pins the token/host pairing docs/scripts.md's GH_HOST entry warns
    # about, for the combination where GH_HOST resolves from this
    # directory's .envrc but CI_CHECKS_GH_TOKEN doesn't.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-token-scoped-elsewhere"},
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert token_calls["view"] == ("", "")
    assert repo_host_calls["view"] == ("<unset>", "octocat.ghe.com")
    # The mismatch this test models: GH_HOST resolved via direnv,
    # CI_CHECKS_GH_TOKEN stayed at its ambient value — the gate withholds
    # the stale token rather than letting it reach the newly-resolved host.
    assert token_calls["watch"] == ("", "")
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert token_calls["json"] == ("", "")
    assert repo_host_calls["json"] == ("<unset>", "octocat.ghe.com")
    assert "GH_HOST resolved via direnv for" in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" in result.stderr
    assert "but CI_CHECKS_GH_TOKEN's resolution did not" in result.stderr


def test_gh_host_resolved_via_direnv_to_mixed_case_ghe_com_still_fires_mismatch_warning(fake_gh, tmp_path):
    # [[ ]] glob matching is case-sensitive by default -- mirrors
    # test_gh_host_resolved_via_direnv_pairs_with_ambient_ci_checks_token
    # above, with GH_HOST resolving to a mixed-case *.ghe.com-shaped value,
    # to pin that the warning's own glob comparison normalizes case first.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-token-scoped-elsewhere"},
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "Octocat.GHE.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert repo_host_calls["watch"] == ("<unset>", "Octocat.GHE.com")
    assert token_calls["watch"] == ("", "")
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" in result.stderr


def test_gh_host_resolved_via_direnv_to_ghes_host_never_fires_mismatch_warning(fake_gh, tmp_path):
    # Boundary regression for the `*.ghe.com` gate: mirrors
    # `test_gh_host_resolved_via_direnv_pairs_with_ambient_ci_checks_token`'s
    # mismatch shape. Here `GH_HOST` resolves to a self-hosted GHES host that
    # doesn't match `*.ghe.com` instead. The warning must stay silent. That
    # proves the gate is a real narrowing, not a no-op that happens to pass
    # every other test's `*.ghe.com`-suffixed fixture.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-token-scoped-elsewhere"},
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "github.mycompany.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert token_calls["watch"] == ("ambient-token-scoped-elsewhere", "")
    assert repo_host_calls["watch"] == ("<unset>", "github.mycompany.com")
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_direnv_supplies_gh_host_and_ci_checks_token_together_from_same_envrc(fake_gh, tmp_path):
    # The configuration docs/scripts.md actually recommends: a single
    # .envrc exporting both GH_HOST and CI_CHECKS_GH_TOKEN together, so both
    # resolve via direnv and the cross-host mismatch warning above has
    # nothing to flag.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_multi_export({
            "GH_HOST": "octocat.ghe.com",
            "CI_CHECKS_GH_TOKEN": "direnv-supplied-token",
        }),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert token_calls["view"] == ("", "")
    assert token_calls["watch"] == ("direnv-supplied-token", "")
    assert token_calls["json"] == ("direnv-supplied-token", "")
    assert repo_host_calls["view"] == ("<unset>", "octocat.ghe.com")
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert repo_host_calls["json"] == ("<unset>", "octocat.ghe.com")
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_ambient_gh_host_reconfirmed_by_direnv_still_fires_cross_host_mismatch_warning(fake_gh, tmp_path):
    # Regression test: GH_HOST was already ambient-correct before the script
    # started (e.g. direnv's own interactive shell hook exported it on an
    # earlier `cd` into this directory), so direnv's resolved value equals
    # ambient. A value-equality flag would never latch here, so the gate
    # would silently never fire, and a stale, differently-scoped
    # CI_CHECKS_GH_TOKEN would reach that GHE host on every gh pr checks
    # call.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={
            "GH_HOST": "octocat.ghe.com",
            "CI_CHECKS_GH_TOKEN": "ambient-token-scoped-elsewhere",
        },
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert token_calls["watch"] == ("", "")
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    # The per-var notice stays silent (resolved value equals ambient), but
    # the presence-based flag it doesn't gate still lets the withholding
    # gate fire.
    assert "ci-watch: GH_HOST resolved via direnv for" not in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" in result.stderr
    assert "but CI_CHECKS_GH_TOKEN's resolution did not" in result.stderr


def test_direnv_reconfirming_ambient_ci_checks_token_value_suppresses_mismatch_warning(fake_gh, tmp_path):
    # Regression test for the false-positive companion to the bypass above.
    # `CI_CHECKS_GH_TOKEN` resolves via direnv to a value identical to what
    # was already ambient (e.g. set once in a parent shell, pinned again by
    # the current `.envrc`). `GH_HOST` resolves to a new value from that
    # same `.envrc`. A value-equality flag would treat `CI_CHECKS_GH_TOKEN`
    # as "did not resolve via direnv" and wrongly warn of a stale-token
    # mismatch even though both vars genuinely resolved via direnv.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "stable-token"},
        direnv_source=_direnv_shim_source_multi_export({
            "GH_HOST": "octocat.ghe.com",
            "CI_CHECKS_GH_TOKEN": "stable-token",
        }),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert token_calls["watch"] == ("stable-token", "")
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_gh_host_resolved_via_direnv_with_no_ci_checks_gh_token_anywhere_suppresses_mismatch_warning(fake_gh, tmp_path):
    # Untested empty-token-guard branch: GH_HOST resolves via direnv while
    # CI_CHECKS_GH_TOKEN is absent from both the ambient env and direnv's own
    # export payload. The mismatch warning's `-n "${CI_CHECKS_GH_TOKEN:-}"`
    # guard must suppress it, since there is no token left to reach the
    # newly-resolved host.
    repo_host_log = tmp_path / "repo_host.log"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        repo_host_log=repo_host_log,
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert "ci-watch: GH_HOST resolved via direnv" in result.stderr
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" not in result.stderr


def test_failed_value_probe_with_succeeding_named_probe_still_withholds_token(fake_gh, tmp_path):
    # The fail-open hole this gate closes: resolve_ci_checks_gh_token makes
    # two independent direnv calls for CI_CHECKS_GH_TOKEN. Here the first
    # (value_probe) fails because direnv exits 0 but emits a syntactically
    # invalid export payload, so CI_CHECKS_GH_TOKEN never gets direnv's
    # actual answer and stays at its stale ambient value. The second
    # (named_probe) still succeeds against the same directory and latches
    # CI_CHECKS_GH_TOKEN_RESOLVED_VIA_DIRENV to 1 regardless. Without
    # CI_CHECKS_GH_TOKEN_VALUE_PROBE_OK, the gate would wrongly read this
    # as "CI_CHECKS_GH_TOKEN resolved via direnv" and never withhold the
    # stale token from the newly-resolved GHE host.
    token_log = tmp_path / "token.log"
    repo_host_log = tmp_path / "repo_host.log"
    counter_file = tmp_path / "direnv_call_count"
    checks = [
        {"name": "tests", "bucket": "pass", "description": "", "link": "", "workflow": "CI"},
    ]
    env = fake_gh(
        watch_output="All checks were successful\n",
        watch_exit=0,
        json_payload=checks,
        token_log=token_log,
        repo_host_log=repo_host_log,
        extra_env={"CI_CHECKS_GH_TOKEN": "stale-token-value-probe-never-confirmed"},
        direnv_source=_direnv_shim_source_fails_first_call_then_static_exports(
            str(counter_file),
            {"CI_CHECKS_GH_TOKEN": "direnv-named-but-unreadable-value", "GH_HOST": "octocat.ghe.com"},
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    token_calls = _parse_token_log(token_log)
    repo_host_calls = _parse_repo_host_log(repo_host_log)
    assert repo_host_calls["watch"] == ("<unset>", "octocat.ghe.com")
    assert token_calls["watch"] == ("", "")
    assert token_calls["json"] == ("", "")
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" in result.stderr
    # The per-token "resolved via direnv" notice must stay silent: value_probe
    # never succeeded, so CI_CHECKS_GH_TOKEN was never assigned direnv's value.
    assert "ci-watch: CI_CHECKS_GH_TOKEN resolved via direnv" not in result.stderr


def test_json_failure_with_token_withheld_by_cross_host_gate_gets_withheld_hint(fake_gh, tmp_path):
    # Distinguishes the withheld-by-gate 403 hint from both
    # test_json_failure_with_token_unset_appends_hint_to_existing_error
    # (never had a token) and test_json_failure_with_token_set_has_no_hint
    # (token present and used) above -- after the gate's `unset
    # CI_CHECKS_GH_TOKEN`, "never had one" and "had one and withheld it" are
    # otherwise indistinguishable at this read site without
    # CI_CHECKS_GH_TOKEN_WITHHELD.
    env = fake_gh(
        watch_output="Some checks are still pending\n",
        watch_exit=8,
        json_fails=True,
        json_stderr="error connecting to api.github.com\n",
        extra_env={"CI_CHECKS_GH_TOKEN": "ambient-token-scoped-elsewhere"},
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 1
    error_lines = [line for line in result.stdout.splitlines() if line.startswith("CI_RESULT: error")]
    assert len(error_lines) == 1
    error_line = error_lines[0]
    assert "error connecting to api.github.com" in error_line
    assert "CI_CHECKS_GH_TOKEN was withheld due to a cross-host mismatch" in error_line
    assert "docs/scripts.md" in error_line
    # Never the generic "no usable CI_CHECKS_GH_TOKEN was found" hint --
    # a token was found, it was deliberately withheld.
    assert "no usable CI_CHECKS_GH_TOKEN was found" not in error_line


def test_ambient_gh_token_reaches_view_and_unwrapped_fallback_despite_withholding(fake_gh, tmp_path):
    # Pins docs/scripts.md's "Known residual gap" bullet: the cross-host
    # mismatch gate withholds only CI_CHECKS_GH_TOKEN. `gh pr view` and
    # gh_with_checks_token's unwrapped fallback both still read the
    # separate ambient GH_TOKEN directly, unresynced and unwithheld,
    # whether CI_CHECKS_GH_TOKEN was withheld by the gate or simply never
    # set. A future change to that boundary, in either direction, should
    # surface here as a failing assertion rather than silent drift.
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
            "GH_TOKEN": "ambient-token-scoped-elsewhere",
            "CI_CHECKS_GH_TOKEN": "stale-token-withheld-by-gate",
        },
        direnv_source=_direnv_shim_source_static_export(
            "GH_HOST", "octocat.ghe.com",
        ),
    )
    result = _run(env, _PR_NUMBER)
    assert result.returncode == 0
    assert "ci-watch: withholding CI_CHECKS_GH_TOKEN" in result.stderr
    calls = _parse_token_log(token_log)
    assert calls["view"] == ("ambient-token-scoped-elsewhere", "")
    assert calls["watch"] == ("ambient-token-scoped-elsewhere", "")
    assert calls["json"] == ("ambient-token-scoped-elsewhere", "")
