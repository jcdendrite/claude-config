"""Tests for pr-diff-against-base.sh.

gh is replaced in every test by a PATH shim answering `gh pr view --json
baseRefName --jq .baseRefName` -- a different, narrower shape than
test_cleanup_merged_branches.py's `_gh_shim_source` (which simulates
`gh pr list --head`), so this file writes its own. Real git operations run
against temporary repos built via conftest.py's shared scaffolding.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import HOOKS_DIR

from .conftest import _commit, _init_repo, _make_feature_branch, _make_repo_with_remote

# Path to the script under test (resolved relative to this file)
_SCRIPT = Path(__file__).parent.parent / "pr-diff-against-base.sh"
_LIB_SH = HOOKS_DIR / "_lib.sh"


def _gh_shim_source(base_ref: str | None) -> str:
    """Return source for a gh shim answering `gh pr view --json baseRefName
    --jq .baseRefName`.

    base_ref=None models `gh pr view` failing (no PR open for this branch,
    or gh not authenticated) -- the shim exits 1 with no stdout, exercising
    pr-diff-against-base.sh's default-branch fallback path.
    """
    body = "sys.exit(1)" if base_ref is None else f"print({base_ref!r})"
    return textwrap.dedent(f"""\
        #!/usr/bin/env python3
        import sys
        args = sys.argv[1:]
        if args[:2] == ["pr", "view"] and "--json" in args and "baseRefName" in args:
            {body}
        else:
            sys.exit(1)
    """)


def _env_with_gh_shim(tmp_path: Path, base_ref: str | None) -> dict:
    """Build an env with a gh shim reporting base_ref prepended to PATH."""
    shim_dir = tmp_path / "gh_shim"
    shim_dir.mkdir()
    gh_shim = shim_dir / "gh"
    gh_shim.write_text(_gh_shim_source(base_ref))
    gh_shim.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join([str(shim_dir), env.get("PATH", "")])
    return env


def _run_script(repo: Path, env: dict, *, record: bool = False) -> subprocess.CompletedProcess:
    args = [str(_SCRIPT)]
    if record:
        args.append("--record")
    return subprocess.run(
        args, cwd=str(repo), env=env, capture_output=True, text=True, check=False,
    )


def _subject_path(env: dict, repo: Path, session_id: str) -> Path:
    """The recorded-subject path --record writes and `write cumulative-review`
    reads -- same repo-hash recipe as _lib.sh's _marker_lib_repo_hash, applied
    to _lib_repo_root's resolution of `repo`. Suffixed with the session id the
    same <repo-hash>.<session-id> way every completion marker is keyed."""
    config_dir = Path(env["CLAUDE_CONFIG_DIR"])
    repo_root = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    repo_hash = hashlib.sha256(repo_root.encode()).hexdigest()
    return config_dir / "cumulative-review-subject-markers" / f"{repo_hash}.{session_id}"


def _seed_session(config_dir: Path, session_id: str, pid: int | None = None) -> None:
    """Write CONFIG_DIR/sessions/<pid> in the two-line format
    capture-session-id.sh writes, so pr-diff-against-base.sh's
    --record can resolve a session id via the same ancestor-walk
    _lib_resolve_session_id uses. Targets CONFIG_DIR directly rather than
    $HOME/.claude, since this file pins CLAUDE_CONFIG_DIR to an isolated tmp
    dir rather than isolating $HOME. Duplicated from
    hooks/tests/conftest.py's helper of the same shape rather than
    cross-imported, per that file's own neither-test-tree-imports-the-other
    convention.

    pid defaults to this test process's own pid: the walk reaches the
    pytest process itself, since pr-diff-against-base.sh runs as its direct
    subprocess with no intermediate shell.
    """
    target_pid = os.getpid() if pid is None else pid
    sessions_dir = config_dir / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(target_pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(target_pid)).write_text(f"{session_id}\n{start_time}\n")


class TestNormalPathAgainstMain:
    def test_diverged_feature_branch_diff_mentions_changed_file(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/add-thing")
        subprocess.run(["git", "checkout", "-q", "feat/add-thing"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "main")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/add-thing" in result.stdout


class TestGhPrViewFailureFallback:
    def test_gh_pr_view_failure_still_diffs_against_main(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/fallback")
        subprocess.run(["git", "checkout", "-q", "feat/fallback"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "+work on feat/fallback" in result.stdout

    def test_gh_pr_view_failure_resolves_non_main_default_branch(self, tmp_path):
        # "trunk" is outside main/master/develop so this only passes via
        # symbolic-ref, not the candidate loop.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="trunk")
        _make_feature_branch(local, "feat/on-trunk", return_to="trunk")
        subprocess.run(["git", "checkout", "-q", "feat/on-trunk"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-trunk" in result.stdout
        assert "defaulting base to trunk" in result.stderr

    def test_gh_pr_view_failure_resolves_slash_containing_default_branch(self, tmp_path):
        # ${origin_head#*/} strips only through the first "/", so a
        # multi-segment default branch name must survive intact.
        local, _bare = _make_repo_with_remote(tmp_path, default_branch="release/1.0")
        _make_feature_branch(local, "feat/on-release", return_to="release/1.0")
        subprocess.run(["git", "checkout", "-q", "feat/on-release"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/on-release" in result.stdout
        assert "defaulting base to release/1.0" in result.stderr


class TestMergeBaseFailure:
    def test_unresolvable_base_ref_aborts_naming_the_ref_on_stderr(self, tmp_path):
        # gh reports a base ref that was never fetched as origin/<name> locally --
        # distinct from the gh-failure fallback above, which always resolves against
        # the local origin/main that _make_repo_with_remote already sets up.
        local, _bare = _make_repo_with_remote(tmp_path)

        env = _env_with_gh_shim(tmp_path, "nonexistent-base")
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "origin/nonexistent-base" in result.stderr


class TestCandidateLoopFallback:
    def test_missing_origin_head_falls_back_to_candidate_loop(self, tmp_path):
        # When origin/HEAD's symref is absent, the main/master/develop
        # candidate loop must still recover origin/main.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/no-symref", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/no-symref"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/no-symref" in result.stdout
        assert "defaulting base to main" in result.stderr

    def test_candidate_loop_prefers_main_when_multiple_candidates_exist(self, tmp_path):
        # main/master/develop are probed in that order, so main must win
        # when both exist as origin branches.
        local, _bare = _make_repo_with_remote(tmp_path)
        subprocess.run(["git", "checkout", "-q", "-b", "master"], cwd=local, check=True)
        subprocess.run(["git", "push", "-q", "origin", "master"], cwd=local, check=True)
        subprocess.run(["git", "checkout", "-q", "main"], cwd=local, check=True)
        subprocess.run(["git", "remote", "set-head", "origin", "--delete"], cwd=local, check=True)
        _make_feature_branch(local, "feat/multi-candidate", return_to="main")
        subprocess.run(["git", "checkout", "-q", "feat/multi-candidate"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/multi-candidate" in result.stdout
        assert "defaulting base to main" in result.stderr


class TestReportedBaseOverridesDefaultBranch:
    def test_stacked_pr_diffs_against_reported_base_not_repo_default(self, tmp_path):
        # gh's reported base must win over the repo's own default branch.
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "staging")
        subprocess.run(["git", "checkout", "-q", "staging"], cwd=local, check=True)
        _make_feature_branch(local, "feat/stacked", return_to="staging")
        subprocess.run(["git", "checkout", "-q", "feat/stacked"], cwd=local, check=True)

        env = _env_with_gh_shim(tmp_path, "staging")
        result = _run_script(local, env)

        assert result.returncode == 0
        assert "+work on feat/stacked" in result.stdout
        assert "-work on staging" in result.stdout
        assert result.stderr == ""


class TestDefaultBranchUnresolvable:
    def test_repo_without_origin_aborts_naming_the_resolution_failure(self, tmp_path):
        # Regression test: no origin remote at all must produce a message
        # naming the resolution failure, not a stale "origin/main" guess.
        local = tmp_path / "no-remote"
        _init_repo(local)
        _commit(local, "init")

        env = _env_with_gh_shim(tmp_path, None)
        result = _run_script(local, env)

        assert result.returncode == 1
        assert result.stdout == ""
        assert "no default branch resolved" in result.stderr


def _lib_hash_diff_text(text: str) -> str:
    """Shell out to the real _lib_hash_diff_text against `text`."""
    result = subprocess.run(
        ["bash", "-c", f'. "{_LIB_SH}"; _lib_hash_diff_text "$1"', "_hash_diff_text", text],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _lib_cumulative_diff_hash(repo_root: str, pr_diff_script: str, env: dict) -> str:
    """Shell out to the real _lib_cumulative_diff_hash against `repo_root`,
    driving PR_DIFF_SCRIPT through `env`'s own gh shim so it resolves the
    same base ref pr-diff-against-base.sh's --record invocation did."""
    result = subprocess.run(
        [
            "bash", "-c",
            f'. "{_LIB_SH}"; _lib_cumulative_diff_hash "$1" "$2"',
            "_cumulative_diff_hash", repo_root, pr_diff_script,
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return result.stdout.strip()


class TestRecordFlag:
    """--record's own behavior. Three invariants, each with its own test
    method below:

    - Must never change the bare invocation's stdout.
    - Must always print the diff before its own resolution can fail.
    - Must produce a subject whose hashed value agrees byte-for-byte with
      _lib_cumulative_diff_hash's -- required because a second hashing
      recipe that strips text differently would make marker.sh's stored
      value and status's recomputed value permanently and silently
      disagree.
    """

    SID = "test-session-record-flag"

    @pytest.fixture(autouse=True)
    def _seeded_session(self, tmp_path):
        # Matches _isolate_transcript_corpus_lookups' own CLAUDE_CONFIG_DIR
        # value (same tmp_path fixture instance) so --record's session-id
        # resolution finds this entry.
        _seed_session(tmp_path / "isolated-claude-config", self.SID)

    def test_bare_invocation_stdout_unchanged_by_record_flag(self, tmp_path):
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/parity")
        subprocess.run(["git", "checkout", "-q", "feat/parity"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        bare_result = _run_script(local, env)
        record_result = _run_script(local, env, record=True)

        assert bare_result.returncode == 0
        assert record_result.returncode == 0
        assert bare_result.stdout == record_result.stdout

    def test_record_writes_subject_matching_stdout_minus_trailing_newline(self, tmp_path):
        """The subject file omits the mechanically appended trailing newline
        stdout carries (see the empty-diff test below for why), so it
        matches stdout with exactly that one newline stripped."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/record")
        subprocess.run(["git", "checkout", "-q", "feat/record"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr

        subject = _subject_path(env, local, self.SID)
        assert subject.read_text() == result.stdout[:-1]

    def test_record_writes_zero_byte_subject_for_a_genuinely_empty_diff(self, tmp_path):
        """A repo already at parity with its merge-base (no feature branch,
        nothing to diff) must record a 0-byte subject -- a format pin on the
        recorded artifact itself. This 0-byte pin is independent of how
        marker.sh judges emptiness, which reads the file through its own
        command-substitution lens, not this raw byte count. Still a
        ground-truth assertion, not a comparison against this script's own
        stdout, which would share any padding bug."""
        local, _bare = _make_repo_with_remote(tmp_path)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        # Bare stdout still carries the mechanically appended newline (see
        # the comment above DIFF_TEXT's declaration) -- only the subject
        # file itself is byte-exact for this 0-byte format pin.
        assert result.stdout == "\n"

        subject = _subject_path(env, local, self.SID)
        assert subject.stat().st_size == 0

    def test_record_overwrites_a_prior_subject_with_fresh_content(self, tmp_path):
        """A second --record must replace the subject with the current diff,
        not append to or leave stale content alongside it -- mktemp+mv
        overwrites the target atomically, but nothing else in the pipeline
        guarantees that without this test."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/re-record")
        subprocess.run(["git", "checkout", "-q", "feat/re-record"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, record=True)
        assert first.returncode == 0, first.stderr
        subject = _subject_path(env, local, self.SID)
        first_content = subject.read_text()

        (local / "second.txt").write_text("second file\n")
        subprocess.run(["git", "add", "second.txt"], cwd=local, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "second commit"], cwd=local, check=True)

        second = _run_script(local, env, record=True)
        assert second.returncode == 0, second.stderr
        second_content = subject.read_text()

        assert second_content == second.stdout[:-1]
        assert second_content != first_content
        assert "second.txt" in second_content

    def test_record_prints_before_any_record_path_failure(self, tmp_path):
        """A CONFIG_DIR resolution failure in the record path must never cost
        the caller the diff it asked for -- the diff already reaches stdout
        before --record's own resolution runs. A relative CLAUDE_CONFIG_DIR
        makes _lib_config_dir fail, per its own documented contract."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/unresolvable-config-dir")
        subprocess.run(
            ["git", "checkout", "-q", "feat/unresolvable-config-dir"], cwd=local, check=True
        )
        env = _env_with_gh_shim(tmp_path, "main")
        env["CLAUDE_CONFIG_DIR"] = "relative-and-unresolvable"

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "subject not recorded" in result.stderr

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_record_path_failure_leaves_prior_subject_intact(self, tmp_path):
        """A failed record pipeline must not touch a subject already on disk
        from a prior successful record -- the mktemp+mv sequence only
        replaces the subject on a fully successful write."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/protect-subject")
        subprocess.run(["git", "checkout", "-q", "feat/protect-subject"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        first = _run_script(local, env, record=True)
        assert first.returncode == 0, first.stderr
        subject = _subject_path(env, local, self.SID)
        prior_content = subject.read_text()

        # Read+execute only: mktemp can no longer create a new temp file in
        # this directory, so the pipeline fails before ever reaching mv.
        subject.parent.chmod(0o555)
        try:
            second = _run_script(local, env, record=True)
        finally:
            subject.parent.chmod(0o755)

        assert second.returncode == 0, (
            "a record-path failure must not change the script's exit code"
        )
        assert "diff --git a/file.txt b/file.txt" in second.stdout
        assert subject.read_text() == prior_content

    def test_recorded_content_hashes_identically_to_lib_cumulative_diff_hash(self, tmp_path):
        """The byte-equality property this comparison exists for: hashing
        the recorded subject through _lib_hash_diff_text must equal
        _lib_cumulative_diff_hash's own value for the same tree, so a
        second, independently-drifting hashing recipe can never make the
        write-side and read-side values silently disagree."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/byte-equality")
        subprocess.run(["git", "checkout", "-q", "feat/byte-equality"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        subject = _subject_path(env, local, self.SID)
        # Trailing newlines stripped, mirroring bash command substitution's
        # own stripping inside _lib_cumulative_diff_hash -- without it this
        # comparison would fail on the newline boundary alone, not on a
        # real recipe mismatch.
        recorded_text = subject.read_bytes().rstrip(b"\n").decode()

        from_subject = _lib_hash_diff_text(recorded_text)

        repo_root = subprocess.run(
            ["git", "-C", str(local), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        from_cumulative_hash = _lib_cumulative_diff_hash(repo_root, str(_SCRIPT), env)

        assert from_subject == from_cumulative_hash


class TestRecordFlagWithoutASession:
    """No test in this class seeds a session file, unlike TestRecordFlag's
    autouse fixture. This exercises --record's session-id-resolution failure
    branch specifically, distinct from TestRecordFlag's own CONFIG_DIR
    failure test above."""

    def test_record_prints_before_session_resolution_failure(self, tmp_path):
        """No live session file exists, so _lib_resolve_session_id fails.
        --record must still print the diff and fail the subject recording
        open, the same posture every other --record failure mode uses."""
        local, _bare = _make_repo_with_remote(tmp_path)
        _make_feature_branch(local, "feat/no-session")
        subprocess.run(["git", "checkout", "-q", "feat/no-session"], cwd=local, check=True)
        env = _env_with_gh_shim(tmp_path, "main")

        result = _run_script(local, env, record=True)
        assert result.returncode == 0, result.stderr
        assert "diff --git a/file.txt b/file.txt" in result.stdout
        assert "subject not recorded" in result.stderr
        subject_dir = Path(env["CLAUDE_CONFIG_DIR"]) / "cumulative-review-subject-markers"
        assert not subject_dir.exists() or list(subject_dir.iterdir()) == []
