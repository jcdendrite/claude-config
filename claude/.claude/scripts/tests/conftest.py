"""Shared git-repo scaffolding helpers for the worktree-cleanup script tests
(test_cleanup_merged_branches.py, test_cleanup_idle_open_pr_worktrees.py),
plus suite-wide transcript-corpus isolation (see the autouse fixture below),
plus the transcript-record fixture builders shared across
test_transcript_analysis.py, test_transcript_cost.py, test_token_analyzer.py,
test_context_composition.py, test_transcript_denials.py,
test_transcript_review_trace.py, test_transcript_read_scope.py,
test_transcript_ledger_common.py, test_transcript_cost_ledger.py,
test_transcript_cost_ledger_record_gates.py, test_transcript_gh_cli.py,
test_transcript_pr_cost_ledger.py, test_transcript_pr_cost.py,
test_transcript_pr_cost_gh.py, test_transcript_pr_cost_export.py,
test_transcript_pr_cost_export_accounts.py, test_transcript_cache_rebuild.py,
test_transcript_cache_rebuild_attribution.py,
test_transcript_cache_rebuild_switch_delta.py,
test_transcript_cache_rebuild_ttl_rules.py,
test_transcript_cache_rebuild_ttl_accumulation.py,
test_transcript_cache_rebuild_ttl_footing.py, test_transcript_audit_routing.py,
test_transcript_audit_routing_shape.py, test_transcript_audit_routing_samples.py,
test_transcript_subagents.py, test_transcript_subagent_mix.py,
test_transcript_subagent_mix_dollars.py, test_transcript_cost_counts.py, and
tests/_cache_rebuild_helpers.py (see the extraction rationale on
_write_jsonl below).

The scaffolding helpers are plain functions, not pytest fixtures — they take
`tmp_path` (or a repo built from it) as an explicit argument rather than
being injected, matching the calling convention already established in
test_cleanup_merged_branches.py. They have no shape-specific dependency on
either script's `gh` query: building a local git repo, a feature branch, and
a worktree is identical regardless of which script is under test.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, SKILLS_DIR, head_sha, init_git_repo
from transcript_analysis import pricing, scope
from transcript_analysis.corpus import SUBAGENT_SUBDIR


def _write_jsonl(path: Path, records: list[dict]) -> None:
    """Write records as one-JSON-object-per-line, transcript-analysis.py's on-disk shape -- shared
    here (with _asst/_user_msg) so test_context_composition.py doesn't re-derive its own,
    possibly-drifting copy of the requestId run-merge shape _dedup_turns_by_request_id relies on."""
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


# ---------------------------------------------------------------------------
# `gh`-shim scaffolding shared by any test that PATH-shims `gh` for a
# script under test, so test_respond_pr_safe_patch.py's own `gh` shim (a
# different API shape -- GET-then-PATCH against a PR review comment, not a
# merge-status query) routes through the same credential-scrubbing helper
# instead of a hand-rolled copy of its own. Each test file keeps its own
# domain-specific shim *source* generator (e.g. _gh_shim_source in
# test_cleanup_merged_branches.py) and passes it to _shimmed_env below.
# ---------------------------------------------------------------------------

# gh-credential env vars that must never leak from a contributor's real
# shell into a test's PATH-shimmed subprocess (see _base_test_env).
# NODE_AUTH_TOKEN is an npm-registry variable, not a gh one, but a container
# that provisions a classic PAT exports it carrying that identical secret,
# so it needs the same scrubbing.
_SENSITIVE_ENV_VARS = frozenset({
    "GH_TOKEN", "GH_HOST", "GH_REPO", "GITHUB_TOKEN",
    "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN", "GH_CONFIG_DIR",
    "CI_CHECKS_GH_TOKEN", "NODE_AUTH_TOKEN",
})


def _base_test_env() -> dict:
    """Inherited env with DIRENV_* and gh-credential vars stripped.

    Left as inherited, `direnv export bash` run from a test's tmp_path
    would emit the *revert* half of a contributor's real DIRENV_* diff,
    restoring a PATH without the test's own shim dir — the script's next
    `gh` call would be the contributor's real gh with their real token,
    against real GitHub.
    """
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("DIRENV_") and key not in _SENSITIVE_ENV_VARS
    }


# Tools the scripts under test (cleanup-merged-branches.sh, ci-watch.sh) and
# _worktree-lib.sh need on a normal (non-lsof, non-usage-error) run — mirrors
# TestGhMissing's min_bin list. mktemp and rm are ci-watch.sh's own additions.
# Both are unconditional on every run, backing its STDERR_FILE capture and
# matching EXIT trap. Symlinking only these into a curated directory keeps the
# absent-direnv PATH free of a real direnv without also losing any other
# tool that happens to share direnv's install directory (e.g. git, via the
# same package-manager prefix).
_TOOLS_NEEDED_WITHOUT_DIRENV = (
    "git", "python3", "bash", "grep", "awk", "sed", "dirname", "mktemp", "rm",
)


def require_direnv() -> None:
    """Guard for a test that needs a real direnv binary, not a shim: skip
    locally when direnv isn't installed, but hard-fail in this repo's own
    CI.

    Checks GITHUB_ACTIONS rather than the generic CI var because it's this
    workflow's own install step being asserted. The hard-fail guarantee
    depends on .github/workflows/tests.yml's "Install stow and direnv"
    step, which installs direnv before tests run.
    """
    if not shutil.which("direnv"):
        if os.environ.get("GITHUB_ACTIONS"):
            pytest.fail("direnv missing in CI — .github/workflows/tests.yml must install it")
        pytest.skip("direnv not installed")


def _curated_path_without_direnv(tmp_path: Path) -> str:
    curated_dir = tmp_path / f"curated_bin_{uuid.uuid4().hex}"
    curated_dir.mkdir()
    for tool in _TOOLS_NEEDED_WITHOUT_DIRENV:
        tool_path = shutil.which(tool)
        if tool_path:
            (curated_dir / tool).symlink_to(tool_path)
    return str(curated_dir)


def _noop_direnv_shim_source() -> str:
    """Default direnv shim installed for every test: `export bash` exits 0
    with no output, modeling a directory with no identity-bearing .envrc.
    Tests exercising direnv's own export payload pass their own source via
    _shimmed_env's direnv_source parameter."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        sys.exit(0)
    """)


def _shimmed_env(
    tmp_path: Path,
    gh_shim_source_text: str,
    *,
    direnv_source: str | None = None,
    direnv_present: bool = True,
) -> dict:
    """Build the credential-scrubbed, PATH-shimmed env every test's `gh`
    invocation must use — the single seam `fake_gh` and every
    hand-rolled shim site route through, so none can skip the
    DIRENV_*/token scrubbing.

    direnv_present=False replaces the inherited PATH with a curated
    directory holding only the tools the script needs, none of them
    `direnv` — deterministic on machines with and without direnv actually
    installed, and immune to direnv sharing an install prefix with a tool
    the script does need (e.g. git).
    """
    shim_dir = tmp_path / f"shim_{uuid.uuid4().hex}"
    shim_dir.mkdir()

    gh_shim = shim_dir / "gh"
    gh_shim.write_text(gh_shim_source_text)
    gh_shim.chmod(0o755)

    if direnv_present:
        direnv_shim = shim_dir / "direnv"
        direnv_shim.write_text(direnv_source or _noop_direnv_shim_source())
        direnv_shim.chmod(0o755)
        base_path = os.environ.get("PATH", "")
    else:
        base_path = _curated_path_without_direnv(tmp_path)

    new_path = os.pathsep.join([str(shim_dir), base_path])
    return {**_base_test_env(), "PATH": new_path}


def _direnv_shim_source_static_export(name: str, value: str) -> str:
    """direnv shim that unconditionally exports one NAME=VALUE on `export
    bash`, regardless of cwd — for tests that only need one export to
    reach (or be safely rejected by) the calling shell. Shared by
    test_cleanup_merged_branches.py and test_ci_watch.py's own direnv-
    resolution tests, so neither carries a second, possibly-drifting copy."""
    quoted_value = shlex.quote(value)
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("export {name}={quoted_value}")
        sys.exit(0)
    """)


def _direnv_shim_source_unconditional_unset(name: str) -> str:
    """direnv shim that unconditionally emits `unset NAME` on `export
    bash`, regardless of cwd — models direnv leaving a container's
    identity behind when the current directory has no matching .envrc.
    Shared by test_cleanup_merged_branches.py and test_ci_watch.py's own
    direnv-resolution tests, so neither carries a second, possibly-drifting
    copy."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("unset {name}")
        sys.exit(0)
    """)


def _direnv_shim_source_exits_nonzero_with_unset_payload(name: str = "GH_TOKEN") -> str:
    """direnv shim modeling a non-`allow`ed .envrc: `export bash` exits 1
    but still writes an unset payload to stdout. The exit-status guard
    load_repo_environment (cleanup-merged-branches.sh) and
    resolve_ci_checks_gh_token (ci-watch.sh) both apply must discard this
    cleanly. name defaults to GH_TOKEN, matching every pre-existing call
    site's fixed payload; ci-watch.sh's own tests pass
    name="CI_CHECKS_GH_TOKEN"."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            print("unset {name}")
            sys.exit(1)
        sys.exit(0)
    """)


def _direnv_shim_source_reads_stdin() -> str:
    """direnv shim modeling an .envrc that reads stdin — if a caller's
    `</dev/null` guard on its `export bash` eval regressed, this call would
    hang waiting for input that never comes. Shared by
    test_cleanup_merged_branches.py and test_ci_watch.py's own stdin-hang
    regression test, so neither carries a second, possibly-drifting copy."""
    return textwrap.dedent("""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            sys.stdin.read()
        sys.exit(0)
    """)


def _direnv_shim_source_stalls_without_reading_stdin(seconds: int) -> str:
    """direnv shim that sleeps `seconds` on `export bash` without reading
    stdin. Unlike _direnv_shim_source_reads_stdin above, this proves
    direnv_export_bash's wall-clock timeout cap fires on its own, not
    merely as a side effect of the `</dev/null` stdin guard."""
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        import time
        args = sys.argv[1:]
        if args[:2] == ["export", "bash"]:
            time.sleep({seconds})
        sys.exit(0)
    """)


# ---------------------------------------------------------------------------
# review-pr scaffolding shared across its own gh-shimmed test files
# (test_review_pr_checkout.py, test_review_pr_diff.py,
# test_review_pr_acquire.py, test_review_pr_finish.py, test_review_pr_post.py,
# test_review_pr_findings_path.py). They live here, matching _shimmed_env's own
# precedent above.
# ---------------------------------------------------------------------------


def _provenance_fields(path: Path) -> dict:
    """Parse a `.provenance` file's key=value lines into a dict, skipping
    the `schema=1` header line -- the shape review-pr-acquire.sh/
    -checkout.sh/-diff.sh write via _lib_write_review_pr_provenance
    (_lib.sh) and marker.sh/marker-clear-stale.py read back by key. Shared
    by every review-pr test file that asserts on written provenance
    content, so none carries its own line-index-based parse that could
    drift from the production key=value shape. Requires the first line to
    be exactly `schema=1`, matching _lib_review_pr_provenance_field's own
    fail-closed read, so a writer regression that drops the header fails
    this parse instead of silently falling through to a fields dict missing
    nothing the caller checks."""
    lines = path.read_text().splitlines()
    assert lines and lines[0] == "schema=1", (
        f"provenance file {path} missing required 'schema=1' header line: {lines[:1]!r}"
    )
    fields: dict = {}
    for line in lines[1:]:
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        fields[key] = value
    return fields


# One line of shim source defining `sanitize_like_gh(name)`, for the review-pr
# scripts' gh shims. Models gh 2.100.0's behavior: a JSON-escaped C0 control
# character other than tab, LF, VT and CR reaches `--jq` as caret notation (ESC
# as `^[`, NUL as `^@`), so it never reaches a script raw.
# `gh api --help` documents the sanitizing itself, which `--allow-escape-sequences`
# turns off. The caret notation and the four exempt characters are not
# documented there and are not independently re-verified.
# The shims' `pulls/<N>` payloads model GitHub's "Get a pull request" response
# (the pulls page of the GitHub REST reference, https://docs.github.com/en/rest/pulls/pulls).
GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE = (
    "sanitize_like_gh = lambda name: ''.join("
    "'^' + chr(ord(c) ^ 0x40) if ord(c) < 32 and c not in '\\t\\n\\x0b\\r' else c for c in name)"
)


def _assert_gh_calls_are_read_only(
    calls: list[list[str]],
    *,
    known_api_endpoint: re.Pattern,
    allowed_pr_subcommands: tuple[str, ...] = ("view",),
) -> None:
    """Allowlists the `gh` call shapes a review-pr script makes. A `gh pr`
    call must use one of `allowed_pr_subcommands`, name its repo with `-R`, and
    for `view` carry `--json`; the guard does not check its other tokens. A
    `gh api <endpoint>` call must use an endpoint matching `known_api_endpoint`,
    followed by nothing or by exactly `--paginate --jq <filter>`, so a method,
    field or input flag in any spelling is rejected without being enumerated.
    Any other `gh` command fails, as does an unanticipated write shape a
    suite's fixtures never modeled.

    Covers only the `gh` invocations the shimmed code paths a suite drives
    actually make; it says nothing about a non-`gh` network write a script
    might issue.
    """
    for args in calls:
        if args[:1] == ["pr"] and len(args) > 1 and args[1] in allowed_pr_subcommands:
            assert "-R" in args, f"gh pr {args[1]} missing -R: {args}"
            if args[1] == "view":
                assert "--json" in args, f"gh pr view missing --json: {args}"
            continue
        if args[:1] == ["api"]:
            endpoint = args[1] if len(args) > 1 else ""
            assert known_api_endpoint.fullmatch(endpoint), f"unexpected gh api endpoint: {args}"
            trailing_tokens = args[2:]
            assert trailing_tokens == [] or (
                len(trailing_tokens) == 3 and trailing_tokens[:2] == ["--paginate", "--jq"]
            ), f"gh api call carries a token outside `--paginate --jq <filter>`: {args}"
            continue
        pytest.fail(f"gh invocation outside the allowlist: {args}")


def _pythonpath_env_that_forges_a_clean_audit(poison_dir: Path, sentinel: Path) -> dict[str, str]:
    """Environment whose PYTHONPATH holds a `json` module that, when the audit
    script is the running program, writes `sentinel` and prints the audit's
    clean document, so an audit not run under `python3 -I` reports "clean" for
    any file list. Any other program (the tests' python `gh` shim) gets the
    standard library's own json."""
    poison_dir.mkdir(parents=True, exist_ok=True)
    (poison_dir / "json.py").write_text(
        "import importlib.util, os, sys, sysconfig\n"
        "if os.path.basename(sys.argv[0]) == 'audit-execution-surface.py':\n"
        f"    open({str(sentinel)!r}, 'w').write('ran')\n"
        "    print('{\"stop\": false, \"matches\": []}')\n"
        "    sys.exit(0)\n"
        "real_init = os.path.join(sysconfig.get_paths()['stdlib'], 'json', '__init__.py')\n"
        "spec = importlib.util.spec_from_file_location(\n"
        "    'json', real_init, submodule_search_locations=[os.path.dirname(real_init)])\n"
        "real_json = importlib.util.module_from_spec(spec)\n"
        "sys.modules['json'] = real_json\n"
        "spec.loader.exec_module(real_json)\n"
    )
    return {"PYTHONPATH": str(poison_dir)}


def _review_pr_audit_limit(name: str) -> int:
    """The display bound `name` (REVIEW_PR_AUDIT_MATCH_LINE_LIMIT or
    REVIEW_PR_AUDIT_PATH_ECHO_LIMIT) as _review-pr-lib.sh defines it, so a
    test sizes its input from the production constant."""
    result = subprocess.run(
        ["bash", "-c", f'. "{SCRIPTS_DIR / "_review-pr-lib.sh"}"; printf "%s" "${name}"'],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return int(result.stdout)


def _review_pr_findings_body_path(home: Path, session_id: str) -> Path:
    """The findings-body path the agent's Write tool targets, resolved through
    the production path helper (`_lib_review_pr_artifact_path`) rather than a
    suffix the test spells out."""
    result = subprocess.run(
        [
            "bash", "-c",
            f'. "{HOOKS_DIR / "_lib.sh"}"; _lib_review_pr_artifact_path "$1" "$2" body',
            "bash", str(home / ".claude"), session_id,
        ],
        capture_output=True, text=True, check=True, timeout=60,
    )
    return Path(result.stdout)


def _assert_finish_and_clear_stale_each_empty_the_active_directory(home: Path, repo: Path, session_id: str) -> None:
    """After a real review-pr flow has left its artifacts under
    `.review-pr-active.d`, both `review-pr-finish.sh` and `marker.sh
    clear-stale` (once the provenance's PID is dead and no live session names
    the session id) leave that directory empty. The directory's contents, not
    a list of suffixes, are the invariant, so an artifact a writer adds that
    neither cleanup knows about fails here. `repo` is the working directory
    the cleanups run from. The findings body is written here because the
    agent's Write tool, not a script, creates it."""
    active_dir = home / ".claude" / ".review-pr-active.d"
    _review_pr_findings_body_path(home, session_id).write_text("findings\n")
    assert len(list(active_dir.iterdir())) >= 3, "control: the flow left its artifacts behind"
    snapshot_dir = home / "active-dir-snapshot"
    shutil.copytree(active_dir, snapshot_dir)
    env = {"HOME": str(home), "PATH": os.environ["PATH"]}

    finish = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "review-pr-finish.sh")],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60,
    )
    assert finish.returncode == 0, finish.stderr
    assert list(active_dir.iterdir()) == []

    shutil.rmtree(active_dir)
    shutil.copytree(snapshot_dir, active_dir)
    ended_process = subprocess.Popen(["true"])
    ended_process.wait()
    provenance = active_dir / f"{session_id}.provenance"
    provenance.write_text(re.sub(r"(?m)^pid=.*$", f"pid={ended_process.pid}", provenance.read_text()))
    shutil.rmtree(home / ".claude" / "sessions")
    clear_stale = subprocess.run(
        ["bash", str(SCRIPTS_DIR / "marker.sh"), "clear-stale"],
        cwd=repo, env=env, capture_output=True, text=True, timeout=60,
    )
    assert clear_stale.returncode == 0, clear_stale.stderr
    assert list(active_dir.iterdir()) == []


def _install_audit_script(home: Path, audit_script_source: Path | None = None) -> None:
    """Symlink the real audit-execution-surface.py into the isolated
    $HOME/.claude/skills/review-pr/ -- the installed-layout path
    review-pr-checkout.sh/review-pr-diff.sh resolve via
    $CONFIG_DIR/skills/review-pr/ (stow-packages.sh: claude-skills/ stows to
    ~/.claude/). Exercises the real predicate rather than a stand-in copy
    that could drift from it. `audit_script_source` redirects the link for a
    test whose own failure could write through it."""
    skill_dir = home / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True, exist_ok=True)
    target = skill_dir / "audit-execution-surface.py"
    # Unlink first so an earlier stand-in in this home never survives as the "real" audit.
    target.unlink(missing_ok=True)
    target.symlink_to(audit_script_source or SKILLS_DIR / "review-pr" / "audit-execution-surface.py")


def _install_audit_script_that_runs(home: Path, script_body: str) -> None:
    """Install a stand-in audit-execution-surface.py at the installed-layout
    path, for a run whose audit must end a way the real one never does."""
    skill_dir = home / ".claude" / "skills" / "review-pr"
    skill_dir.mkdir(parents=True, exist_ok=True)
    target = skill_dir / "audit-execution-surface.py"
    # Unlink first: writing through the symlink _install_audit_script creates would overwrite the tracked audit script.
    target.unlink(missing_ok=True)
    target.write_text(script_body)


def _seed_session(home: Path, session_id: str, pid: int | None = None) -> None:
    """Write $HOME/.claude/sessions/<pid> in the two-line format
    capture-session-id.sh writes -- every review-pr script under test here
    resolves its own session id (and, for the provenance-writing scripts,
    its own Claude PID) by walking process ancestors via
    _lib_resolve_claude_pid, so a test exercising a success path needs a
    live session file for that walk to find.

    pid defaults to this test process's own pid: marker.sh (invoked by
    several of these scripts) resolves its session id by walking process
    ancestors, and when it runs as a subprocess of pytest, that walk
    reaches the pytest process itself.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = home / ".claude" / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,  # same bound as the _SUBPROCESS_TIMEOUT_SECONDS the review-pr test modules declare
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


def _git_shim_that_fails_on_worktree_subcommand(
    tmp_path: Path, *subcommands: str, exit_status: int = 1
) -> Path:
    """A `git` PATH shim that fails only "git ... worktree <subcommand>"
    for each named subcommand (e.g. `add`, `remove`, `prune`) with
    `exit_status`, delegating every other invocation to the real git
    binary -- shared by the review-pr checkout and finish tests, each
    proving a failed worktree operation surfaces through that script's own
    exit code and stderr message. At least one subcommand is required: an
    empty list would emit a syntactically invalid shim."""
    if not subcommands:
        raise ValueError("name at least one worktree subcommand for the shim to fail")
    real_git = shutil.which("git")
    failing_conditions = " || ".join(
        f'[[ "$*" == *{shlex.quote(f"worktree {subcommand}")}* ]]' for subcommand in subcommands
    )
    shim_dir = tmp_path / f"git_shim_{uuid.uuid4().hex}"
    shim_dir.mkdir()
    git_shim = shim_dir / "git"
    git_shim.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        if {failing_conditions}; then
          echo "synthetic worktree failure" >&2
          exit {exit_status}
        fi
        exec {shlex.quote(real_git)} "$@"
    """))
    git_shim.chmod(0o755)
    return shim_dir


def _build_repo_with_pr_ref(
    tmp_path: Path, owner_repo: str = "foo/bar", pr_number: str = "42",
) -> tuple[Path, str]:
    """A local repo with an `origin` remote and a PR ref pushed directly to
    it under `refs/pull/<N>/head` -- mirrors GitHub's synthetic per-PR ref,
    which is never a normal branch on the base repo. Returns (repo, pr_sha)
    with the working copy left checked out at the pre-PR commit, so a
    successful checkout's own worktree content is what proves the PR
    commit landed, not the main checkout's.

    The bare remote's own path embeds owner_repo's two path segments (e.g.
    `.../remote/foo/bar`), so review-pr-checkout.sh's/review-pr-diff.sh's own
    origin-identity check (comparing $1's owner/repo against the worktree's
    actual origin) sees the same value callers pass as $1 -- the same
    last-two-path-segments shape a real `https://github.com/<owner>/<repo>.git`
    origin parses to.
    """
    bare = tmp_path / "remote" / owner_repo
    bare.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "--bare"], cwd=bare, check=True)

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("main\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=repo, check=True)
    subprocess.run(["git", "push", "-q", "origin", "HEAD:refs/heads/main"], cwd=repo, check=True)
    main_sha = head_sha(repo)

    (repo / "pr_file.txt").write_text("pr change\n")
    subprocess.run(["git", "add", "pr_file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "pr commit"], cwd=repo, check=True)
    pr_sha = head_sha(repo)
    subprocess.run(["git", "push", "-q", "origin", f"HEAD:refs/pull/{pr_number}/head"], cwd=repo, check=True)
    subprocess.run(["git", "reset", "-q", "--hard", main_sha], cwd=repo, check=True)

    return repo, pr_sha


def _write_subagent_jsonl(
    proj: Path, session_id: str, agent_id: str, records: list[dict]
) -> None:
    """Write records to the split subagent layout: <session_id>/subagents/<agent_id>.jsonl."""
    subdir = proj / session_id / SUBAGENT_SUBDIR
    subdir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(subdir / f"{agent_id}.jsonl", records)


def _write_subagent_dispatch(
    proj: Path, session_id: str, agent_id: str, tool_use_id: str, records: list[dict],
    *, agent_type: str = "staff-backend-engineer", description: str = "review",
    requested_model: str | None = None,
) -> None:
    """Write both the .jsonl and its paired .meta.json for a synthetic subagent
    dispatch — _write_subagent_jsonl (above) writes only the .jsonl, never the
    meta.json sidecar reviewer-yield's and subagent-mix's dispatch joins read.
    Matches the real on-disk shape: {"agentType", "description", "toolUseId",
    "spawnDepth"}. requested_model, when given, adds meta.json's own "model"
    key — absent by default, matching a dispatch that requested no explicit
    model."""
    _write_subagent_jsonl(proj, session_id, agent_id, records)
    subdir = proj / session_id / SUBAGENT_SUBDIR
    meta = {"agentType": agent_type, "description": description, "toolUseId": tool_use_id, "spawnDepth": 1}
    if requested_model is not None:
        meta["model"] = requested_model
    (subdir / f"{agent_id}.meta.json").write_text(json.dumps(meta))


def _edit_use(tool_id: str, *, path: str = "/foo.py") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Edit", "input": {"file_path": path}}


def _write_use(tool_id: str, content: str, *, path: str = "/scratch/findings.md") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Write", "input": {"file_path": path, "content": content}}


def _write_cost_root(base: Path, name: str, proj_slug: str, session_id: str, records: list[dict]) -> Path:
    """Build one --config-dir root's project-dir tree — same shape as
    fake_projects' own PROJECTS_DIR (the root directly contains project-slug
    subdirectories, no extra projects/ layer), parameterized so multi-root
    tests can build more than one root under the same tmp_path."""
    root = base / name
    proj = root / proj_slug
    proj.mkdir(parents=True)
    _write_jsonl(proj / f"{session_id}.jsonl", records)
    return root


def _asst(
    model: str,
    *,
    branch: str = "main",
    sidechain: bool = False,
    ts: str | None = None,
    content: list | None = None,
    request_id: str | None = None,
) -> dict:
    rec: dict = {
        "type": "assistant",
        "gitBranch": branch,
        "isSidechain": sidechain,
        "message": {"model": model, "content": content or [], "usage": {}},
    }
    if ts:
        rec["timestamp"] = ts
    if request_id is not None:
        rec["requestId"] = request_id
    return rec


def _user_msg(content, *, branch: str = "main", ts: str | None = None) -> dict:
    rec: dict = {"type": "user", "gitBranch": branch, "message": {"content": content}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _bash_use(tool_id: str, command: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}


def _skill_block(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def _slash_user(skill: str, *, branch: str = "main", ts: str | None = None) -> dict:
    return _user_msg(f"<command-name>/{skill}</command-name>", branch=branch, ts=ts)


def _tool_result(tool_id: str, text: str) -> dict:
    return {"type": "tool_result", "tool_use_id": tool_id, "content": text}


def _compact_boundary_rec() -> dict:
    return {"type": "system", "subtype": "compact_boundary"}


def _agent_use(tool_id: str, subagent_type: str, *, tool_name: str = "Agent", prompt: str = "y") -> dict:
    return {
        "type": "tool_use",
        "id": tool_id,
        "name": tool_name,
        "input": {"subagent_type": subagent_type, "description": "x", "prompt": prompt},
    }


def _ledger_row(
    *,
    round: int | None,
    disposition: str,
    finding: str = "some finding",
    rationale: str = "why",
    source: str = "n/a",
    authoring_agent: str = "",
    authoring_effort: str = "",
    schema_version: int = 2,
    event_time: object = "2026-08-01T10:00:00Z",
    session_id: str | None = None,
) -> dict:
    """One review-narrative-ledger row, review-ledger.sh's own row shape.
    round=None omits the `round` key entirely rather than setting it null,
    modeling a pre-schema-v2 legacy row. review-ledger.sh itself never
    writes a null round. session_id=None omits the `session_id` key, the
    shape of a row written before rows carried one (schema v2). Pass
    schema_version=3 alongside a session_id for a row that carries one.
    review-ledger.sh's _LEDGER_SCHEMA_VERSION is the writer's own version, and
    the readers read no version field."""
    row = {
        "schema_version": schema_version,
        "finding": finding,
        "disposition": disposition,
        "rationale": rationale,
        "source": source,
        "authoring_agent": authoring_agent,
        "authoring_effort": authoring_effort,
        "event_time": event_time,
    }
    if round is not None:
        row["round"] = round
    if session_id is not None:
        row["session_id"] = session_id
    return row


def _write_named_ledger_file(
    config_dir_root: Path, repo_hash: str, name_slot: str, rows: list[dict],
) -> Path:
    ledger_dir = config_dir_root / "review-narrative-ledger"
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{repo_hash}.{name_slot}.jsonl"
    _write_jsonl(path, rows)
    return path


def _write_ledger_file(
    config_dir_root: Path, session_id: str, rows: list[dict], *, repo_hash: str = "0" * 64,
) -> Path:
    """Write one session-keyed review-narrative-ledger file for a synthetic
    session, mirroring review-ledger.sh's own
    $LEDGER_DIR/$REPO_HASH.$SESSION_ID.jsonl naming under
    <config_dir_root>/review-narrative-ledger/. author_outcome.py attributes
    a row without a `session_id` to the session in this filename, so a
    legacy-shaped row needs no session_id here. The repo-hash prefix is
    irrelevant to that attribution, so a fixed placeholder is fine.
    """
    return _write_named_ledger_file(config_dir_root, repo_hash, session_id, rows)


def _write_branch_ledger_file(
    config_dir_root: Path, branch_hash: str, rows: list[dict], *, repo_hash: str = "0" * 64,
) -> Path:
    """Write one branch-keyed review-narrative-ledger file,
    $LEDGER_DIR/$REPO_HASH.$BRANCH_HASH.jsonl. Its rows must carry their own
    `session_id`: nothing in the filename says which session wrote them."""
    return _write_named_ledger_file(config_dir_root, repo_hash, branch_hash, rows)


def _opus(
    content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z",
    request_id: str | None = None,
) -> dict:
    """Build an Opus assistant record with explicit usage values -- shared by
    audit-routing's and cost's own tests."""
    rec = _asst(
        "claude-opus-4-7",
        branch="main",
        ts=ts,
        content=content,
        request_id=request_id,
    )
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _priced_opus(
    content: list, *, out: int = 100, cr: int = 0, ts: str = "2026-05-19T10:00:00.000Z",
    model: str = "claude-opus-5", request_id: str | None = None,
) -> dict:
    """Build a priced-Opus assistant record (default claude-opus-5, in
    _MODEL_BASE_INPUT_RATES) for audit-routing's dollar-headline tests —
    _opus()'s claude-opus-4-7 is deliberately unpriced."""
    rec = _asst(model, branch="main", ts=ts, content=content, request_id=request_id)
    rec["message"]["usage"] = {
        "input_tokens": 50,
        "output_tokens": out,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": cr,
    }
    return rec


def _priced(
    model: str,
    *,
    input: int = 0,
    cache_read: int = 0,
    ephemeral_1h: int = 0,
    ephemeral_5m: int = 0,
    output: int = 0,
    flat_cache_creation: int | None = None,
    ts: str = "2026-05-19T10:00:00.000Z",
    branch: str = "main",
    request_id: str | None = None,
    content: list | None = None,
    speed: str | None = None,
    inference_geo: str | None = None,
) -> dict:
    """Build an assistant record with explicit priced usage fields for cost tests.

    flat_cache_creation=None (the default) emits the nested cache_creation
    block from ephemeral_1h/ephemeral_5m, with the flat cache_creation_input_tokens
    field set to their sum — matching every real usage record sampled, where the
    two always agree. flat_cache_creation=N omits the nested block entirely and
    emits only the flat field (the pre-nested-block fallback shape), ignoring
    ephemeral_1h/ephemeral_5m. branch="main" by default so every pre-existing
    call site (predating --branches) is unaffected. content=None (the default)
    keeps every pre-existing call site's empty-content shape; rearm-backtest's
    boundary-detection tests pass real tool_use/tool_result blocks instead,
    needing both a realistic content shape and known, priced usage in one record.
    speed/inference_geo default to None (field absent), matching every usage
    record sampled outside fast-mode/data-residency requests.
    """
    rec = _asst(model, branch=branch, ts=ts, content=content if content is not None else [], request_id=request_id)
    usage: dict = {
        "input_tokens": input,
        "output_tokens": output,
        "cache_read_input_tokens": cache_read,
    }
    if flat_cache_creation is not None:
        usage["cache_creation_input_tokens"] = flat_cache_creation
    else:
        usage["cache_creation_input_tokens"] = ephemeral_1h + ephemeral_5m
        usage["cache_creation"] = {
            "ephemeral_1h_input_tokens": ephemeral_1h,
            "ephemeral_5m_input_tokens": ephemeral_5m,
        }
    if speed is not None:
        usage["speed"] = speed
    if inference_geo is not None:
        usage["inference_geo"] = inference_geo
    rec["message"]["usage"] = usage
    return rec


def _priced_sidechain_asst(
    model: str, *, input_tokens: int = 0, output_tokens: int = 0, cache_read_tokens: int = 0,
    ts: str | None = None, branch: str = "main",
) -> dict:
    """Build a sidechain assistant record with explicit, flat-priced usage
    fields, for subagent-mix's Actual $/Counterfactual $ dollar-column tests
    -- a sidechain counterpart to TestCost's own _priced (cache-write-split
    fidelity is irrelevant to these tests' hand-computed input-token math)."""
    rec = _asst(model, branch=branch, sidechain=True, ts=ts, content=[])
    rec["message"]["usage"] = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_input_tokens": cache_read_tokens,
        "cache_creation_input_tokens": 0,
    }
    return rec


def _table_cols(out: str, *, header_contains: str, row_contains: str | list[str],
                drop_leading_labels: int = 0,
                max_labels: int | None = None,
                row_startswith: bool = False,
                occurrence: int | None = None) -> dict[str, str]:
    """Map column-label -> cell value for the data row matching `row_contains`.

    Anchors column positions to the header row (the line containing
    `header_contains`) instead of hard-coding indices, so a column reorder in
    the source output fails meaningfully rather than silently reading the wrong
    column.

    Precondition: every asserted column's header label AND cell value is a
    single whitespace token (true for all leading label/count columns; trailing
    free-text columns like "Top subagent types" are not assertable this way and
    are not asserted by any test). `drop_leading_labels` lets a caller declare
    that the row deliberately suppresses N leading left-aligned labels (the only
    case: cmd_subagents continuation rows blank the Branch column) — declared
    explicitly per call, never inferred.
    `max_labels` limits labels to only the first N single-token columns, required
    for tables whose header contains a trailing multi-word column name (e.g.,
    cmd_subagent_mix's "Top subagent types") whose tokens would otherwise inflate
    the label count beyond the data row's token count.
    `row_startswith=True` matches only lines where `row_contains` appears at
    column 0, filtering out indented summary/annotation lines that also contain
    the same text (e.g., cmd_skill_invocation summary section).

    Row search is always scoped to one table's own section -- from its header
    line through the next blank line or the next header-containing line -- so
    a second table elsewhere in the same output that happens to share row
    text (e.g. both tables use "main"/"sidechain" thread labels, or both
    start "AgentType") is never mistaken for this one's data. Without
    `occurrence`, `header_contains` must match exactly one line in the whole
    output. `occurrence` scopes to the Nth (1-indexed) line containing
    `header_contains` instead, for a header substring that legitimately
    repeats across tables (e.g. reviewer-yield's Table 1 and Table 2 both
    start "AgentType"). A table's own rule line ("-" * len(header), printed
    immediately after the header) is pure dashes, so it never matches a
    blank-line or header-match boundary check and needs no special-casing to
    stay inside the section.
    `row_contains` accepts a single string or a sequence of strings, all of
    which must appear on the matched line — needed once a table can hold more
    than one row per entity (e.g. two bucket rows per agent type), where a
    bare entity-name substring would match more than one line even within a
    single table's section.

    Fails loudly (AssertionError) when exactly one header / data row isn't
    found, or when token counts don't line up — a silent mismatch would
    reintroduce the GH-363 bug class under a new cause.
    """
    lines = out.splitlines()
    header_indices = [i for i, ln in enumerate(lines) if header_contains in ln]
    if occurrence is None:
        assert len(header_indices) == 1, f"header match not unique for {header_contains!r}: {len(header_indices)}"
        start = header_indices[0]
    else:
        assert len(header_indices) >= occurrence, (
            f"header occurrence {occurrence} requested but only {len(header_indices)} "
            f"found for {header_contains!r}"
        )
        start = header_indices[occurrence - 1]
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if not lines[i].strip() or header_contains in lines[i]:
            end = i
            break
    section_lines = lines[start:end]

    headers = [ln for ln in section_lines if header_contains in ln]
    assert len(headers) == 1, f"header match not unique for {header_contains!r}: {len(headers)}"
    header = headers[0]

    needles = (row_contains,) if isinstance(row_contains, str) else tuple(row_contains)
    if row_startswith:
        rows = [
            ln for ln in section_lines
            if ln != header and ln.startswith(needles[0]) and all(n in ln for n in needles)
        ]
    else:
        rows = [ln for ln in section_lines if ln != header and all(n in ln for n in needles)]
    assert len(rows) == 1, f"row match not unique for {row_contains!r}: {len(rows)}"
    labels = header.split()[drop_leading_labels:]
    if max_labels is not None:
        labels = labels[:max_labels]
    values = rows[0].split()
    assert len(values) >= len(labels), f"row has fewer cells than labels: {rows[0]!r}"
    return dict(zip(labels, values, strict=False))


def _extract_grand_total(out: str) -> float:
    """Read cost's grand-total row ('total  $X.XX') from the token-class table."""
    match = re.search(r"^total\s+([\d,]+\.\d\d)\s*$", out, re.MULTILINE)
    assert match is not None, "grand total row not found in output"
    return float(match.group(1).replace(",", ""))


def _cost_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
    by_project: bool = False,
    branches: str | None = None,
    summary: bool = False,
    share_only: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
        "by_project": by_project,
        "branches": branches,
        "summary": summary,
        "share_only": share_only,
    })()


def _cost_ledger_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    record: bool = False,
    force: bool = False,
    note: str = "",
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "record": record,
        "force": force,
        "note": note,
    })()


def _cost_ledger_row(**overrides) -> dict:
    """A complete, valid row dict with sensible defaults, overridden per test."""
    row = {
        "week": "2026-W20", "machine": "m1", "rates": "2026-08-02", "usd": 12.5,
        "context_pct": 10.0, "opus_pct": 5.0, "ge200k_pct": 10.0,
        "denials": 2, "reviewer_gap_pp": 3.5, "note": "baseline",
    }
    row.update(overrides)
    return row


def _two_declared_roots(tmp_path, monkeypatch) -> list[Path]:
    """Active profile (acct-a) plus one declared root (acct-b, via
    TRANSCRIPT_CONFIG_DIRS_FILE) -- the minimal multi-root setup where a call
    site that forgot to thread `roots` is distinguishable from one that
    threaded it correctly (both look identical at one root, since
    _resolve_project_scope's own internal default is also (PROJECTS_DIR,)).
    Pins both PROJECTS_DIR (_resolve_scan_roots' base, used by 18 of the 19
    funnel subcommands) and CLAUDE_CONFIG_DIR (config_dir(), which
    _resolve_cost_roots reads independently for cost/context-distribution) at
    the same acct-a, so every subcommand agrees on the same two-root list."""
    acct_a = tmp_path / "acct-a"
    (acct_a / "projects").mkdir(parents=True)
    acct_b = tmp_path / "acct-b"
    (acct_b / "projects").mkdir(parents=True)
    monkeypatch.setattr(scope, "PROJECTS_DIR", acct_a / "projects")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(acct_a))
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{acct_b}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))
    return [acct_a / "projects", acct_b / "projects"]


def _cost_trend_args(
    *, projects: str = "*", this_repo: bool = False, extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects, "this_repo": this_repo, "extra_config_dirs": extra_config_dirs,
    })()


def _context_distribution_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    no_redact: bool = False,
    extra_config_dirs: list[str] | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "no_redact": no_redact,
        "extra_config_dirs": extra_config_dirs,
    })()


def _audit_routing_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    top: int = 20,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "top": top,
        "redact": redact,
    })()


def _reviewer_yield_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    since: str | None = None,
    until: str | None = None,
    redact: bool = False,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "since": since,
        "until": until,
        "redact": redact,
    })()


def _hook_deny(
    hook_name: str, *, stringified: bool = False, branch: str = "main", ts: str | None = None
) -> dict:
    """Build an attachment/hook_blocking_error record using the real transcript shape.

    Real transcripts nest the human-readable denial text in a "blockingError" key
    inside the blockingError dict (alongside a "command" key).

    When stringified=True, the outer blockingError value is a JSON-encoded string
    of that dict rather than the dict itself (as seen in some real transcripts).

    branch/ts mirror the sibling _hook_deny_current — a synthetic attachment
    denial carries its own gitBranch/timestamp too, so tests can distinguish an
    implementation that reads the record's own branch from one that only
    exercises the carry-forward path.
    """
    human_message = f"Hook '{hook_name}' blocked the operation"
    error_dict = {"blockingError": human_message, "command": "git commit -m x"}
    blocking_error = json.dumps(error_dict) if stringified else error_dict
    rec: dict = {
        "type": "attachment",
        "gitBranch": branch,
        "attachment": {
            "type": "hook_blocking_error",
            "hookName": hook_name,
            "toolUseID": f"toolu_{hook_name[:8]}",
            "blockingError": blocking_error,
        },
    }
    if ts:
        rec["timestamp"] = ts
    return rec


def _hook_deny_current(
    message: str,
    *,
    tool_id: str = "toolu_cur",
    ts: str | None = None,
    branch: str = "main",
    tool_denial_kind: str | None = None,
    is_error: bool = True,
) -> dict:
    """Build a current-format hook denial.

    Newer Claude Code transcripts no longer emit a hook_blocking_error
    attachment record — a denial surfaces only as a user record whose
    tool_result block carries is_error and the denial text. tool_denial_kind
    mirrors the real toolDenialKind field, which lives on this parent user
    record, not on the tool_result block itself.
    """
    rec: dict = {
        "type": "user",
        "gitBranch": branch,
        "isSidechain": False,
        "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": tool_id,
             "content": message, "is_error": is_error},
        ]},
    }
    if ts:
        rec["timestamp"] = ts
    if tool_denial_kind:
        rec["toolDenialKind"] = tool_denial_kind
    return rec


def _review_trace_args(
    *,
    projects: str = "*",
    this_repo: bool = False,
    branches: str | None = None,
    since: str | None = None,
    until: str | None = None,
    deny_only: bool = False,
    deny_summary: bool = False,
    skill: str | None = None,
) -> object:
    return type("A", (), {
        "projects": projects,
        "this_repo": this_repo,
        "branches": branches,
        "since": since,
        "until": until,
        "deny_only": deny_only,
        "deny_summary": deny_summary,
        "skill": skill,
    })()


def _skill_use(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def _read_use(tool_id: str, file_path: str) -> dict:
    """Build a Read tool_use block with the given file_path."""
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": file_path}}


def _exit_plan_mode(tool_id: str = "epm1") -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "ExitPlanMode", "input": {}}


def _thinking_block() -> dict:
    return {"type": "thinking", "thinking": "some thought"}


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch, request):
    """Isolated single-account corpus: patches scope.PROJECTS_DIR and
    scope.config_dir on the calling test file's own loaded transcript-analysis.py
    module (`request.module._mod`) -- every test file loads its own independent
    copy via spec_from_file_location, but each copy's `from transcript_analysis
    import scope` resolves to the one process-wide transcript_analysis.scope
    module, so patching `mod.scope` here is visible regardless of which file's
    `_mod` requested the fixture.

    _resolve_cost_roots (subagents, subagent-mix, cost, context-distribution)
    derives its default root from a fresh config_dir() call, not from the
    PROJECTS_DIR patch above — without this, a subcommand routed through
    _resolve_cost_roots would silently fall back to this machine's real
    config dir instead of this fixture's isolated tmp_path. spend-over-threshold
    and rearm-backtest stay in the shim (not yet moved into the package) and call config_dir()
    via their own separate import, so mod.config_dir is patched too. cost_ledger,
    ledger_common, and pr_cost_ledger each bind config_dir by name from _config_dir, mirroring
    scope.py's own binding, so all three are patched too -- five bindings of the same initial
    value, each the sole read path for its own still-independent call sites.
    """
    mod = request.module._mod
    projects = tmp_path / "projects"
    proj = projects / "-home-user-testrepo"
    proj.mkdir(parents=True)
    monkeypatch.setattr(mod.scope, "PROJECTS_DIR", projects)
    monkeypatch.setattr(mod.scope, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mod.cost_ledger, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mod.ledger_common, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(mod.pr_cost_ledger, "config_dir", lambda: tmp_path)
    return proj


@pytest.fixture()
def fake_config_dir_factory(tmp_path):
    """Factory for extra --config-dir roots: each call builds a fresh config
    dir (with its own projects/ subdirectory) under tmp_path, independent of
    fake_projects' own PROJECTS_DIR — cost's multi-root tests use this to
    build a second (and third) account profile."""
    def _make(name: str) -> Path:
        config_dir_path = tmp_path / name
        (config_dir_path / "projects").mkdir(parents=True)
        return config_dir_path
    return _make


@pytest.fixture()
def cost_ledger_file(tmp_path, monkeypatch, request):
    """Isolated docs/cost-ledger.md: a fresh file with the canonical header/
    separator and zero data rows, matching the real committed file's own
    shape. _cost_ledger_path is monkeypatched (on the calling test file's own
    `_mod.cost_ledger` -- see fake_projects above for why `request.module._mod`)
    so every test in this section reads/writes this file, never this repo's
    own tracked ledger."""
    mod = request.module._mod
    ledger_path = tmp_path / "cost-ledger.md"
    ledger_path.write_text(
        "# Cost-trend ledger\n\n"
        + mod.cost_ledger._COST_LEDGER_HEADER_LINE + "\n"
        + mod.cost_ledger._COST_LEDGER_SEPARATOR_LINE + "\n"
    )
    monkeypatch.setattr(mod.cost_ledger, "_cost_ledger_path", lambda: ledger_path)
    return ledger_path


@pytest.fixture()
def cost_ledger_enabled(tmp_path, monkeypatch, fake_projects, request):
    """Isolated config dir carrying the cost-ledger opt-in sentinel and a
    seeded machine identity. Shared by test_transcript_cost_ledger.py's and
    test_transcript_cost_ledger_record_gates.py's own cost-ledger tests and
    test_transcript_ledger_common.py's TestMachineIdentity (see fake_projects
    above for why `request.module._mod`).

    - Sets `CLAUDE_CONFIG_DIR` explicitly rather than relying on
      `_isolate_transcript_corpus_lookups`'s coincidental tmp-path match.
      `_cost_ledger_report`'s sentinel check resolves `config_dir` through
      `_config.py`'s own binding, not `_mod`'s, so patching
      `_mod.cost_ledger.config_dir` alone has no effect on it.
    - The env var alone is not sufficient either. `fake_projects`
      monkeypatches `_mod.cost_ledger.config_dir` to its own `tmp_path`,
      which wins over the env var since it never re-reads the environment.
    - Declaring `fake_projects` as this fixture's own parameter (not just
      requested alongside it) makes pytest's fixture graph run it first,
      regardless of a test's own parameter order.
    - `mod.cost_ledger.config_dir` and `mod.ledger_common.config_dir` are
      both patched again here so they and `_config.config_enabled()`'s
      env-var-based resolution all agree on the same directory.
    """
    mod = request.module._mod
    cfg_dir = tmp_path / "isolated-claude-config"
    cfg_dir.mkdir()
    (cfg_dir / ".cost-ledger-enabled").touch()
    (cfg_dir / mod.ledger_common._MACHINE_IDENTITY_FILENAME).write_text("7e57c0de")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(mod.cost_ledger, "config_dir", lambda: cfg_dir)
    monkeypatch.setattr(mod.ledger_common, "config_dir", lambda: cfg_dir)
    return cfg_dir


@pytest.fixture(autouse=True)
def _isolate_transcript_corpus_lookups(tmp_path, monkeypatch):
    """Pin both env vars transcript-analysis.py's root resolution reads, so no
    test in this suite can accidentally scan or declare against this
    workstation's real ~/.claude.

    Pinning only TRANSCRIPT_CONFIG_DIRS_FILE is insufficient:
    _resolve_scan_roots/_resolve_cost_roots' base is PROJECTS_DIR/config_dir()
    (config_dir()/"projects" at import), and config_dir() reads $HOME when
    CLAUDE_CONFIG_DIR is unset — on a real workstation with a populated
    ~/.claude, an unpinned test would scan the real corpus; in CI, where
    $HOME/.claude is simply absent, the same test would pass for an unrelated
    reason. Both must be pinned for the isolation to be real rather than
    CI-only. TRANSCRIPT_CONFIG_DIRS_FILE points at a nonexistent path by
    default (declared_transcript_roots() treats a missing file as a silent
    single-root no-op), so an ordinary test never sees a declared root unless
    it opts in by writing that path itself.

    test_post_crash_sessions.py's own
    test_main_smoke_against_live_environment_no_traceback is a deliberate,
    documented exception to this isolation (it asserts no hardcoded counts,
    only a clean run) — this fixture does not special-case it, since pinning
    CLAUDE_CONFIG_DIR to an empty tmp dir still satisfies that test's actual
    assertions.
    """
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "isolated-claude-config"))
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(tmp_path / "nonexistent-transcript-config-dirs"))


@pytest.fixture(autouse=True)
def _reset_pricing_format_drift_flags(monkeypatch):
    """Reset pricing's two per-process format-drift flags before every test.

    pytest-xdist's default --dist=load doesn't group by file, so a test that
    trips _warn_if_run_usage_drift or _warn_if_subagent_format_drift (e.g.
    test_transcript_analysis.py's and test_transcript_reviewer_yield.py's own
    drift-canary tests) would otherwise leak a fired flag into a later
    _cost_report test sharing the same worker, intermittently tripping its
    PRICING INTEGRITY banner for an unrelated reason. monkeypatch.setattr
    (not a bare assignment) so the prior value is restored on teardown too.
    """
    monkeypatch.setattr(pricing, "_usage_drift_warned", False)
    monkeypatch.setattr(pricing, "_subagent_format_drift_detected", False)


def _init_repo(path: Path, initial_branch: str = "main") -> None:
    """Initialise a git repo on `initial_branch` with a test identity and no commit."""
    # Defaulting the branch to "main" avoids depending on the system's
    # init.defaultBranch setting, which varies across git versions and CI environments.
    init_git_repo(path, branch=initial_branch)


def _commit(repo: Path, message: str = "commit") -> None:
    (repo / "file.txt").write_text(message + "\n")
    subprocess.run(["git", "add", "file.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repo, check=True)


def _make_repo_with_remote(tmp_path: Path, default_branch: str = "main") -> tuple[Path, Path]:
    """Return (local_repo, bare_remote) with origin configured and default branch set."""
    bare = tmp_path / "remote.git"
    bare.mkdir()
    subprocess.run(["git", "init", "--bare", "-q", f"--initial-branch={default_branch}"], cwd=bare, check=True)

    local = tmp_path / "local"
    _init_repo(local, initial_branch=default_branch)
    _commit(local, "init")
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=local, check=True)
    subprocess.run(["git", "push", "-q", "-u", "origin", default_branch], cwd=local, check=True)
    # Set origin/HEAD so a caller relying on it can resolve the default branch
    subprocess.run(["git", "remote", "set-head", "origin", default_branch], cwd=local, check=True)
    return local, bare


def _make_feature_branch(repo: Path, branch_name: str, return_to: str = "main") -> None:
    """Create and push a feature branch in repo, then return to return_to."""
    subprocess.run(["git", "checkout", "-q", "-b", branch_name], cwd=repo, check=True)
    _commit(repo, f"work on {branch_name}")
    subprocess.run(["git", "push", "-q", "origin", branch_name], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", return_to], cwd=repo, check=True)


def _make_worktree(repo: Path, branch_name: str, wt_path: Path) -> None:
    """Add a linked worktree for branch_name at wt_path."""
    subprocess.run(
        ["git", "worktree", "add", str(wt_path), branch_name],
        cwd=repo,
        check=True,
    )


def _dead_pid() -> int:
    """Return a pid that is guaranteed not to be running."""
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid
