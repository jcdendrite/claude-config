"""Tests for review-pr-diff.sh -- the no-checkout review path for a
restricted (cross-repo or first-time-contributor) PR that
review-pr-checkout.sh's trust block refuses. Mirrors
review-pr-checkout.sh's self-derivation discipline (own PR-identity parse,
own origin-identity check, own paginated file list, own double headRefOid
fetch) minus everything that only matters once code lands on disk.

The `gh` CLI is replaced by a PATH shim that records every invocation it
receives, matching test_review_pr_checkout.py's own shim pattern -- git is
left real (not shimmed).
"""
from __future__ import annotations

import json
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import SCRIPTS_DIR

from .conftest import (
    GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE,
    _build_repo_with_pr_ref,
    _install_audit_script,
    _install_audit_script_that_runs,
    _provenance_fields,
    _seed_session,
    _shimmed_env,
)

SCRIPT = SCRIPTS_DIR / "review-pr-diff.sh"
OWNER_REPO = "foo/bar"
PR_NUMBER = "42"
PR_IDENTITY = f"{OWNER_REPO}#{PR_NUMBER}"
SID = "test-session-review-pr-diff"

# A harness bound so a hung bash or interpreter fails one test instead of the suite.
_SUBPROCESS_TIMEOUT_SECONDS = 60


@pytest.fixture
def isolated_home(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    return home


@pytest.fixture
def repo_with_pr_ref(tmp_path):
    return _build_repo_with_pr_ref(tmp_path, owner_repo=OWNER_REPO, pr_number=PR_NUMBER)


def _is_listing_call(call: list[str], suffix: str) -> bool:
    """True for a `gh api` call to the pulls listing ending in `suffix`,
    whatever query string it carries."""
    return call[:1] == ["api"] and call[1].split("?", 1)[0].endswith(suffix)


def _diff_text_for(files: list[str]) -> str:
    """A minimal but structurally valid unified diff: one `diff --git`
    header per file."""
    parts = []
    for f in files:
        parts.append(f"diff --git a/{f} b/{f}\nindex 000..111 100644\n--- a/{f}\n+++ b/{f}\n@@ -0,0 +1 @@\n+x\n")
    return "".join(parts)


def _gh_shim_source(
    call_log: Path,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    diff_text: str | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    head_ref_oid_second: str | None = None,
    fail_diff: bool = False,
    diff_error_text: str | None = None,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    fail_rest: bool = False,
    files_failure_exit_status: int = 1,
) -> str:
    """gh shim recording every invocation, matching
    test_review_pr_checkout.py's own shim shape. `diff_text` defaults to a
    structurally valid diff matching `files` (one `diff --git` header per
    file) unless explicitly overridden. `fail_diff` exits 1 on the `gh pr
    diff` call, printing `diff_error_text` to stderr if given -- models a
    `gh` failure whose stderr could echo request parameters, which the
    script must never capture into the `.diff` file or its own output.
    `gh api .../pulls/N` (no trailing `/files`) returns the REST payload's
    own `changed_files`, which defaults to `len(files)` and is set apart
    from `files` to model a truncated or padded listing (`omit_changed_files`
    drops it, `fail_rest` exits 1). The files listing prints one JSON string
    per line under the `@json` jq filter, as real `gh --jq` does for a
    string result: raw UTF-8, with a control character rendered in caret
    notation, as gh 2.100.0 is modeled to do (see the shim constant in
    conftest.py). The listing call must carry `--paginate` and
    `per_page=100`; the shim exits 97 otherwise, so a script dropping either
    fails the run. `fail_files` exits
    `files_failure_exit_status` on the listing call."""
    resolved_diff_text = diff_text if diff_text is not None else _diff_text_for(files or [])
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import json
        import os
        import sys

        CALL_LOG = {str(call_log)!r}
        HEAD_REF_OID = {head_ref_oid!r}
        HEAD_REF_OID_SECOND = {head_ref_oid_second!r}
        FILES = {list(files or [])!r}
        DIFF_TEXT = {resolved_diff_text!r}
        FAIL_PR_VIEW = {fail_pr_view!r}
        FAIL_FILES = {fail_files!r}
        FAIL_DIFF = {fail_diff!r}
        DIFF_ERROR_TEXT = {diff_error_text!r}
        CHANGED_FILES = {len(files or []) if changed_files is None else changed_files!r}
        OMIT_CHANGED_FILES = {omit_changed_files!r}
        FAIL_REST = {fail_rest!r}
        FILES_FAILURE_EXIT_STATUS = {files_failure_exit_status!r}
        {GH_CONTROL_CHARACTER_SANITIZER_SHIM_LINE}
        args = sys.argv[1:]
        prior_pr_view_calls = 0
        if os.path.exists(CALL_LOG):
            with open(CALL_LOG) as f:
                for line in f:
                    if not line.strip():
                        continue
                    if json.loads(line)["args"][:2] == ["pr", "view"]:
                        prior_pr_view_calls += 1
        record = {{"args": args}}
        with open(CALL_LOG, "a") as f:
            f.write(json.dumps(record) + chr(10))
        if args[:2] == ["pr", "view"]:
            if FAIL_PR_VIEW:
                sys.exit(1)
            oid = HEAD_REF_OID
            if HEAD_REF_OID_SECOND is not None and prior_pr_view_calls >= 1:
                oid = HEAD_REF_OID_SECOND
            if oid:
                print(oid)
            sys.exit(0)
        if args[:2] == ["pr", "diff"]:
            if FAIL_DIFF:
                if DIFF_ERROR_TEXT:
                    print(DIFF_ERROR_TEXT, file=sys.stderr)
                sys.exit(1)
            sys.stdout.write(DIFF_TEXT)
            sys.exit(0)
        if args[:1] == ["api"] and not args[1].split("?")[0].endswith("/files"):
            if FAIL_REST:
                sys.exit(1)
            payload = {{}} if OMIT_CHANGED_FILES else {{"changed_files": CHANGED_FILES}}
            print(json.dumps(payload))
            sys.exit(0)
        if args[:1] == ["api"]:
            if "--paginate" not in args or not args[1].endswith("?per_page=100"):
                print("shim: listing call without --paginate and per_page=100", file=sys.stderr)
                sys.exit(97)
            if FAIL_FILES:
                sys.exit(FILES_FAILURE_EXIT_STATUS)
            encode = (
                (lambda name: json.dumps(name, ensure_ascii=False))
                if ".[].filename | @json" in args else str
            )
            for f in FILES:
                sys.stdout.buffer.write((encode(sanitize_like_gh(f)) + chr(10)).encode("utf-8"))
            sys.exit(0)
        sys.exit(0)
    """)


def _read_calls(call_log: Path) -> list[list[str]]:
    if not call_log.exists():
        return []
    return [json.loads(line)["args"] for line in call_log.read_text().splitlines() if line]


def _local_pr_ref_names(repo: Path) -> str:
    return subprocess.run(
        ["git", "for-each-ref", "refs/review-pr"], cwd=repo, capture_output=True, text=True, check=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    ).stdout.strip()


def _review_worktrees(repo: Path) -> list[Path]:
    """Every review-pr-* entry under repo's .claude/worktrees, the directory
    review-pr-checkout.sh names its worktrees in."""
    worktrees_dir = repo / ".claude" / "worktrees"
    return sorted(worktrees_dir.glob("review-pr-*")) if worktrees_dir.is_dir() else []


def _run(
    cwd: Path,
    home: Path,
    args: list[str],
    tmp_path: Path,
    *,
    head_ref_oid: str | None = None,
    files: list[str] | None = None,
    diff_text: str | None = None,
    fail_pr_view: bool = False,
    fail_files: bool = False,
    head_ref_oid_second: str | None = None,
    fail_diff: bool = False,
    diff_error_text: str | None = None,
    changed_files: int | str | None = None,
    omit_changed_files: bool = False,
    fail_rest: bool = False,
    files_failure_exit_status: int = 1,
) -> tuple[subprocess.CompletedProcess, Path]:
    _seed_session(home, SID)
    call_log = tmp_path / "gh_calls.jsonl"
    env = {
        **_shimmed_env(
            tmp_path,
            _gh_shim_source(
                call_log, head_ref_oid, files, diff_text, fail_pr_view, fail_files,
                head_ref_oid_second, fail_diff, diff_error_text,
                changed_files, omit_changed_files, fail_rest, files_failure_exit_status,
            ),
        ),
        "HOME": str(home),
    }
    env.pop("CLAUDE_CONFIG_DIR", None)
    result = subprocess.run(
        ["bash", str(SCRIPT), *args], cwd=cwd, env=env, capture_output=True, text=True,
        timeout=_SUBPROCESS_TIMEOUT_SECONDS,
    )
    return result, call_log


class TestUsageErrors:
    @pytest.mark.parametrize("args", [[], ["foo/bar#42", "extra"], ["no-hash-or-slash"], ["../..#5"]])
    def test_invalid_argv_exits_two_with_no_gh_call(self, isolated_home, repo_with_pr_ref, tmp_path, args):
        repo, _ = repo_with_pr_ref
        result, call_log = _run(repo, isolated_home, args, tmp_path)
        assert result.returncode == 2
        assert _read_calls(call_log) == []


class TestOriginMismatch:
    def test_owner_repo_differing_from_origin_aborts_before_any_gh_call(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [f"decoy-owner/decoy-repo#{PR_NUMBER}"], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "origin" in result.stderr
        assert _read_calls(call_log) == []


class TestInitialHeadRefOidFetchFailure:
    def test_failed_initial_headrefoid_fetch_aborts_before_any_artifact_is_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, _ = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, fail_pr_view=True, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "could not fetch" in result.stderr
        assert "headRefOid" in result.stderr
        assert result.stdout == ""
        active_dir = isolated_home / ".claude" / ".review-pr-active.d"
        assert not active_dir.exists() or list(active_dir.glob(f"{SID}.*")) == []
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))


class TestMissingAuditScript:
    def test_uninstalled_skill_directory_aborts_before_any_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"]
        )
        assert result.returncode == 2, result.stderr
        assert "audit script not found" in result.stderr
        assert _read_calls(call_log) == []


class TestHeadRefOidDrift:
    def test_headrefoid_change_across_the_two_fetches_aborts(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, head_ref_oid_second="f" * 40, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "force-push" in result.stderr
        calls = _read_calls(call_log)
        assert not any(c[:2] == ["pr", "diff"] for c in calls), (
            "a drift-detected abort must never reach the diff fetch"
        )


class TestNoCheckoutNoWorktreeNoLocalRef:
    def test_restricted_pr_produces_a_diff_file_and_diff_only_provenance(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr

        diff_path = Path(result.stdout.strip())
        assert diff_path == isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert diff_path.exists()
        assert "diff --git a/a.py b/a.py" in diff_path.read_text()

        provenance = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.provenance"
        fields = _provenance_fields(provenance)
        assert fields["pr_identity"] == PR_IDENTITY
        assert fields["head_ref_oid"] == pr_sha
        assert fields["pid"].isdigit()
        assert fields["mode"] == "diff-only"

        assert _local_pr_ref_names(repo) == "", "diff-only mode must never fetch a local PR ref"
        assert _review_worktrees(repo) == []


class TestActiveDirectoryCreationFailure:
    def test_a_file_where_the_active_directory_belongs_aborts_with_exit_two_and_writes_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """`mkdir -p` cannot create a directory over a regular file. Under
        `set -e` an unguarded call would exit with mkdir's own status 1."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        active_path = isolated_home / ".claude" / ".review-pr-active.d"
        active_path.parent.mkdir(exist_ok=True)
        active_path.write_text("not a directory\n")
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "could not create the active directory" in result.stderr
        assert result.stdout == ""
        assert active_path.read_text() == "not a directory\n", "pre-existing file is left untouched"


class TestAuditHitReportedNotFatal:
    def test_execution_surface_hit_is_reported_on_stderr_but_exits_zero(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """Distinct from review-pr-checkout.sh's own audit stop: nothing
        lands on disk here, so the audit predicate has no subject to
        protect, and its hit becomes a mandatory pre-seeded finding
        instead."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", ".mcp.json"],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert ".mcp.json" in result.stderr
        diff_path = Path(result.stdout.strip())
        assert diff_path.exists()


class TestAuditThatReturnsNoVerdictIsNotAFinding:
    """A finding means the audit positively returned `stop: true`. A tool that
    failed to run aborts with exit 2 and the audit's stderr shown, so a clean
    PR is never reported as blocked because python3 is missing."""

    @pytest.mark.parametrize(
        "audit_script_body",
        [
            "import sys\nsys.exit(127)\n",
            "raise RuntimeError('audit-tool-crashed')\n",
            "import os, signal\nos.kill(os.getpid(), signal.SIGKILL)\n",
            "import sys\nprint('not json')\nsys.exit(1)\n",
            "import sys\nprint('{\"stop\": false, \"matches\": []}')\nsys.exit(1)\n",
        ],
        ids=["python3-missing-status", "uncaught-exception", "sigkill", "status-1-non-json", "status-1-stop-false"],
    )
    def test_a_failed_audit_aborts_with_no_finding_and_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert "returned no verdict (exit " in result.stderr
        assert "AUDIT_FINDING" not in result.stderr
        assert result.stdout == ""
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    def test_the_failed_audits_stderr_is_shown(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script_that_runs(isolated_home, "raise RuntimeError('audit-tool-crashed')\n")
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2
        assert "audit-tool-crashed" in result.stderr

    def test_a_positive_stop_verdict_is_reported_as_a_finding_and_the_diff_is_still_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script_that_runs(
            isolated_home,
            "import sys\n"
            "print('{\"stop\": true, \"matches\": [{\"path\": \"a.py\", \"reason\": \"stand-in reason\"}]}')\n"
            "sys.exit(1)\n",
        )
        repo, pr_sha = repo_with_pr_ref
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert "a.py: stand-in reason" in result.stderr
        assert Path(result.stdout.strip()).exists()


class TestAuditThatExitsCleanWithoutAVerdictIsNotClean:
    """Exit status 0 alone is not a clean audit: an empty or truncated audit
    script also exits 0, and reporting nothing would read as "audited, nothing
    matched". Only the audit's exact clean document proceeds to the diff."""

    @pytest.mark.parametrize(
        "audit_script_body,first_stdout_line",
        [
            ("", ""),
            ("print('not json')\nprint('second line')\n", "not json"),
            ("print('{\"stop\": true, \"matches\": []}')\n", '{"stop": true, "matches": []}'),
        ],
        ids=["empty-script", "non-json-stdout", "stop-true-at-status-0"],
    )
    def test_a_zero_status_audit_without_a_clean_verdict_aborts_and_writes_no_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path, audit_script_body, first_stdout_line
    ):
        _install_audit_script_that_runs(isolated_home, audit_script_body)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 2, result.stderr
        assert f"returned no verdict (exit 0, first line of stdout: {first_stdout_line})" in result.stderr
        assert "second line" not in result.stderr
        assert "AUDIT_FINDING" not in result.stderr
        assert result.stdout == ""
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))
        assert not list((isolated_home / ".claude" / ".review-pr-active.d").glob("*.diff"))


class TestDiffTextIsWrittenAsFetched:
    def test_a_diff_whose_header_count_differs_from_the_file_count_is_still_written(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        """A file changing between a regular file and a symlink appears as two
        `diff --git` headers for one path, so the header count is not a
        completeness signal and no check counts them."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        typechange_diff = _diff_text_for(["link.txt", "link.txt"])
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["link.txt"], diff_text=typechange_diff,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).read_text().count("diff --git a/link.txt b/link.txt") == 2


class TestChangedFilesPrecheck:
    """`gh pr diff` cannot serve a PR past 300 changed files, so the script
    stops with a named message before fetching a listing or a diff."""

    def test_over_three_hundred_changed_files_stops_before_the_listing_and_the_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], changed_files=301,
        )
        assert result.returncode == 2, result.stderr
        assert "301" in result.stderr and "300" in result.stderr
        calls = _read_calls(call_log)
        assert not any(_is_listing_call(c, "/files") for c in calls)
        assert not any(c[:2] == ["pr", "diff"] for c in calls)

    def test_exactly_three_hundred_changed_files_still_produces_a_diff(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        three_hundred_files = [f"src/file_{n}.py" for n in range(300)]
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=three_hundred_files,
        )
        assert result.returncode == 0, result.stderr
        assert Path(result.stdout.strip()).exists()

    def test_missing_changed_files_count_aborts_before_the_listing(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], omit_changed_files=True,
        )
        assert result.returncode == 2, result.stderr
        assert "changed_files" in result.stderr
        assert not any(_is_listing_call(c, "/files") for c in _read_calls(call_log))

    def test_rest_payload_fetch_failure_aborts(self, isolated_home, repo_with_pr_ref, tmp_path):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_rest=True,
        )
        assert result.returncode == 2, result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))


class TestFileListIntegrity:
    def test_listing_shorter_than_changed_files_aborts_before_the_diff_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], changed_files=2,
        )
        assert result.returncode == 2
        assert "truncated, or the PR changed while it was being fetched" in result.stderr
        assert "Retry" in result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    @pytest.mark.parametrize(
        "exit_status,status_wording",
        [(1, "failed (exit 1)"), (124, "timed out"), (137, "failed (exit 137)")],
        ids=["gh-failed", "cap-kill-124", "sigkill-137"],
    )
    def test_listing_failure_aborts_before_the_diff_fetch(
        self, isolated_home, repo_with_pr_ref, tmp_path, exit_status, status_wording
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_files=True, files_failure_exit_status=exit_status,
        )
        assert result.returncode == 2
        assert f"gh api --paginate {status_wording}" in result.stderr
        assert not any(c[:2] == ["pr", "diff"] for c in _read_calls(call_log))

    def test_listing_is_fetched_paginated_at_the_maximum_page_size_one_json_string_per_name(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path, head_ref_oid=pr_sha, files=["a.py"],
        )
        assert result.returncode == 0, result.stderr
        files_calls = [c for c in _read_calls(call_log) if _is_listing_call(c, "/files")]
        assert len(files_calls) == 1
        files_call = files_calls[0]
        assert "--paginate" in files_call
        assert files_call[1].endswith("?per_page=100")
        assert ".[].filename | @json" in files_call

    @pytest.mark.parametrize(
        "listed_name",
        [
            "docs/notes.txt\nevil.sh",
            "src/a^[b.py",
            'docs/caf\u00e9 "notes".md',
        ],
        ids=["newline", "caret", "non-ascii-quote-space"],
    )
    def test_no_name_is_refused_for_its_content_and_each_reaches_the_audit_intact(
        self, isolated_home, repo_with_pr_ref, tmp_path, listed_name
    ):
        """This path reports audit hits rather than stopping on them, and
        nothing here splits names on a delimiter, so no name is refused. A
        name under an audited path is echoed by the audit finding, which
        shows the decoded name arrived whole."""
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        audited_name = f".claude/hooks/{listed_name}"
        result, _ = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py", audited_name],
        )
        assert result.returncode == 0, result.stderr
        assert "AUDIT_FINDING" in result.stderr
        assert audited_name in result.stderr


class TestGhFailureNeverBypassesTheScrub:
    """A `gh` failure's stderr/error output must never be captured verbatim
    into the `.diff` file -- GitHub API error payloads occasionally echo
    request parameters, and this posting-adjacent artifact is not covered
    by deny-private-project-refs.sh's own gated command set."""

    def test_gh_pr_diff_failure_error_text_never_reaches_the_diff_file_or_output(
        self, isolated_home, repo_with_pr_ref, tmp_path
    ):
        _install_audit_script(isolated_home)
        repo, pr_sha = repo_with_pr_ref
        secret_shaped_text = "leaked-token=ghp_" + "AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHHIIII"
        result, call_log = _run(
            repo, isolated_home, [PR_IDENTITY], tmp_path,
            head_ref_oid=pr_sha, files=["a.py"], fail_diff=True, diff_error_text=secret_shaped_text,
        )
        assert result.returncode == 2, result.stderr
        assert secret_shaped_text not in result.stdout
        assert secret_shaped_text not in result.stderr
        diff_path = isolated_home / ".claude" / ".review-pr-active.d" / f"{SID}.diff"
        assert not diff_path.exists()
