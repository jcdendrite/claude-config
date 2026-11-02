"""scope.py's scan-traversal, scan-gap and glob-selection classes; test_transcript_scope.py holds scope.py's other tests."""
import argparse
import errno
import os
import stat
from collections import Counter

import pytest
from transcript_analysis import corpus, scope

from .conftest import _asst, _write_jsonl


class TestScanRootTranscripts:
    """Direct unit tests for _scan_root_transcripts — neither its glob-count
    behavior nor its per-file skipped-counting was exercised at this layer
    before review; every prior assertion on 'skipped' asserted 0."""

    def test_unreadable_root_raises_permission_error(self, tmp_path):
        """os.access is an explicit probe, not reliance on Path.glob to raise
        — glob silently swallows OSError while walking an unreadable
        directory rather than propagating it (verified empirically during
        review), so this pins the probe itself, not glob's behavior."""
        root = tmp_path / "locked"
        root.mkdir()
        os.chmod(root, 0o000)
        try:
            with pytest.raises(PermissionError):
                scope._scan_root_transcripts(root, "*")
        finally:
            os.chmod(root, 0o755)

    def test_readable_root_with_no_matches_returns_zero_zero(self, tmp_path):
        root = tmp_path / "empty"
        root.mkdir()
        assert scope._scan_root_transcripts(root, "*") == (0, 0)

    def test_skipped_counts_unreadable_file_separately_from_scanned(self, tmp_path):
        proj = tmp_path / "-home-user-repo"
        proj.mkdir()
        readable = proj / "readable.jsonl"
        readable.write_text("{}\n")
        locked = proj / "locked.jsonl"
        locked.write_text("{}\n")
        os.chmod(locked, 0o000)
        try:
            scanned, skipped = scope._scan_root_transcripts(tmp_path, "*")
        finally:
            os.chmod(locked, 0o644)
        assert (scanned, skipped) == (2, 1)

    def test_slugs_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The slugs branch (--this-repo's path) must dedupe the same way the
        glob branch does via _iter_scoped_sessions — a symlinked slug aliasing
        another slug's directory must not double the transcript count."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = scope._scan_root_transcripts(
            tmp_path, "*", slugs=["-home-user-repo", "-home-user-repo-alias"]
        )
        assert scanned == 1

    def test_glob_mode_dedupes_symlinked_alias_by_resolved_path(self, tmp_path):
        """The glob branch (--projects' default path) must dedupe the same
        way the slugs branch does — review found this branch's own dedup was
        missing, an asymmetry between the two branches of one function
        introduced when the slugs branch was added later for --this-repo."""
        real_proj = tmp_path / "-home-user-repo"
        real_proj.mkdir()
        (real_proj / "sess.jsonl").write_text("{}\n")
        alias = tmp_path / "-home-user-repo-alias"
        alias.symlink_to(real_proj)
        scanned, _skipped = scope._scan_root_transcripts(tmp_path, "*")
        assert scanned == 1

    def test_glob_mode_rejects_a_parent_traversing_projects_value(self, tmp_path):
        """A '..' component in `projects_glob` is a real parent-directory step
        for Path.glob, not a no-op: this function must count nothing for it
        instead of counting a sibling account's own transcripts -- the same
        guarantee corpus.iter_sessions gives its own glob match."""
        root = tmp_path / "acct-a"
        root.mkdir()
        sibling = tmp_path / "acct-b"
        sibling.mkdir()
        (sibling / "secret.jsonl").write_text("{}\n")
        scanned, skipped = scope._scan_root_transcripts(root, "../acct-b")
        assert (scanned, skipped) == (0, 0)

    def test_symlink_loop_project_dir_is_silently_skipped_not_raised(self, tmp_path):
        """A symlink-loop project dir reads as absent (its stat fails with ELOOP), so
        _scan_root_transcripts skips it and counts the healthy sibling
        without raising. Its two callers in cost.py only catch
        PermissionError."""
        proj_open = tmp_path / "-repo-open"
        proj_open.mkdir(parents=True)
        (proj_open / "sess.jsonl").write_text("{}\n")
        loop_link = tmp_path / "-repo-loop"
        loop_link.symlink_to(loop_link)
        scanned, skipped = scope._scan_root_transcripts(tmp_path, "*")
        assert (scanned, skipped) == (1, 0)

    @pytest.mark.parametrize("mode", ["glob", "slugs"])
    def test_project_dir_symlinked_outside_the_root_is_counted(self, tmp_path, mode):
        """Only a '..' in the raw glob is rejected. With a single scan root,
        a project directory symlinked to a readable location outside `root`
        stays in scope, in both the glob and the exact-slug branch, matching
        iter_sessions. Multi-root consumers that attribute each session to a
        root do not tolerate it (see the pooled-layer tests in
        test_transcript_review_rounds.py)."""
        root = tmp_path / "acct-a"
        root.mkdir()
        outside = tmp_path / "elsewhere" / "-repo-relocated"
        outside.mkdir(parents=True)
        (outside / "sess.jsonl").write_text("{}\n")
        (root / "-repo-relocated").symlink_to(outside)
        slugs = ["-repo-relocated"] if mode == "slugs" else None
        assert scope._scan_root_transcripts(root, "*", slugs=slugs) == (1, 0)

    def test_symlink_loop_transcript_is_counted_as_skipped_not_raised(self, tmp_path):
        """A looped .jsonl symlink fails open() with OSError, so it is
        counted under `skipped` alongside the healthy transcript instead of
        aborting the scan."""
        proj = tmp_path / "-repo-main"
        proj.mkdir()
        (proj / "sess.jsonl").write_text("{}\n")
        loop_link = proj / "loop.jsonl"
        loop_link.symlink_to(loop_link)
        assert scope._scan_root_transcripts(tmp_path, "*") == (2, 1)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_through_sealed_ancestor_is_silently_skipped_not_raised(self, tmp_path):
        """A project dir reached through a sealed intermediate directory makes
        its stat raise PermissionError (an OSError subclass).
        _scan_root_transcripts skips it without raising and still counts the
        healthy sibling project dir."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        (proj_open / "sess.jsonl").write_text("{}\n")
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            scanned, skipped = scope._scan_root_transcripts(root, "*")
        finally:
            os.chmod(sealed_ancestor, 0o755)
        assert (scanned, skipped) == (1, 0)


class TestIterScopedSessionsUnreadableRoot:
    """Security regression: root.iterdir() raises PermissionError on an
    unreadable root with no exception handling in the pre-fix code -- an
    uncaught traceback that prints the raw path, bypassing --redact
    entirely. Reachable via --this-repo (now multi-root by default) on any
    subcommand funneling through _iter_scoped_sessions. Must be caught,
    reported to stderr without the raw path, and the scan must continue
    across the remaining roots rather than crash."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_root_is_skipped_without_crash_or_path_leak(self, tmp_path, capsys):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])

        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        try:
            sessions = list(scope._iter_scoped_sessions(["-repo-main"], False, roots=[root_a, root_b]))
        finally:
            os.chmod(root_b, 0o755)  # restore before tmp_path teardown

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}  # root_a's session still found -- no crash

        err = capsys.readouterr().err
        assert str(root_b) not in err  # the raw unreadable path is never printed
        assert "skipping" in err


def _assert_scan_error_chain_suppressed(excinfo) -> None:
    """`_path_stripped_scan_error`'s callers raise `from None` so the raw
    path-bearing original exception never surfaces via Python's implicit
    exception-chain printer when the new exception reaches a CLI's
    uncaught top level. Both attributes must hold for `from None` to be
    doing that job."""
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True


class TestScanGapCounter:
    """scope.py's opt-in scan-gap counter (_list_dir_recording_gaps,
    _failed_transcript_read_is_gap, _iter_project_dir_sessions). Each level
    of the traversal (root, project dir, session file) records one tag per
    unreadable directory or transcript it skips. A missing path or a
    non-directory/non-regular entry stays an empty scope, not a gap."""

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_root_records_root_level_gap_and_still_yields_readable_root(self, tmp_path):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions(
                [root_a, root_b], "*", False, scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_b, 0o755)  # restore before tmp_path teardown

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}
        assert scan_gaps == Counter({scope._SCAN_GAP_ROOT: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_dir_records_project_dir_level_gap(self, tmp_path):
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_project_dir_symlink_loop_is_skipped_with_no_gap(self, tmp_path):
        """A self-referencing symlink makes the stat fail with ELOOP (which
        `_stat_mode_unless_absent` reads as absent), so `_dedup_new_project_dirs` skips it:
        no gap is recorded and the healthy sibling is still read. Distinct
        from the sealed-directory test above, where the OSError is
        recorded as a gap."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter()

    def test_project_dir_symlink_loop_without_scan_gaps_is_skipped_without_raising(self, tmp_path):
        """Every non-pooled caller (_iter_scoped_sessions, and
        _iter_glob_scoped_sessions when called without scan_gaps) passes
        scan_gaps=None. A symlink-loop project dir reads as absent, so it is
        skipped there too instead of raising."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        assert list(scope._iter_glob_scoped_sessions([root], "*", False)) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_symlink_through_sealed_ancestor_records_project_dir_level_gap(self, tmp_path):
        """A candidate that is itself a readable child of an already-readable
        root can still fail its stat with PermissionError (an OSError
        subclass) when it symlinks through a sealed intermediate directory
        further down the target path. Distinct from
        the symlink-loop test above, where the stat's ELOOP reads as absent and the
        candidate is skipped without a gap."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(sealed_ancestor, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({scope._SCAN_GAP_PROJECT_DIR: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_symlink_through_sealed_ancestor_without_scan_gaps_propagates_the_exception(self, tmp_path):
        """A non-pooled caller passing no scan_gaps must see the stat's
        PermissionError propagate uncaught, with the raw
        proj_through_sealed path stripped from the message."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            with pytest.raises(PermissionError) as excinfo:
                list(scope._iter_glob_scoped_sessions([root], "*", False))
            assert str(proj_through_sealed) not in str(excinfo.value)
            _assert_scan_error_chain_suppressed(excinfo)
        finally:
            os.chmod(sealed_ancestor, 0o755)

    def test_iter_scoped_sessions_symlink_loop_is_skipped_without_raising(self, tmp_path):
        """The slug-matched sibling of
        test_project_dir_symlink_loop_without_scan_gaps_is_skipped_without_raising:
        _iter_scoped_sessions selects project dirs by exact slug match rather
        than glob, but shares _dedup_new_project_dirs, so a symlink-loop
        project dir is skipped there too."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        loop_link = root / "-repo-loop"
        loop_link.symlink_to(loop_link)
        assert list(scope._iter_scoped_sessions(["-repo-loop"], False, roots=[root])) == []

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_symlink_through_sealed_ancestor_without_scan_gaps_propagates_the_exception(
        self, tmp_path,
    ):
        """The slug-matched sibling of
        test_project_dir_symlink_through_sealed_ancestor_without_scan_gaps_propagates_
        the_exception: _iter_scoped_sessions shares _dedup_new_project_dirs, so a
        non-pooled caller passing no scan_gaps must see the stat's PermissionError
        propagate uncaught here too, raw path stripped too."""
        root = tmp_path / "acct-a"
        root.mkdir(parents=True)
        sealed_ancestor = tmp_path / "sealed-ancestor"
        target = sealed_ancestor / "target-dir"
        target.mkdir(parents=True)
        proj_through_sealed = root / "-repo-through-sealed"
        proj_through_sealed.symlink_to(target)
        os.chmod(sealed_ancestor, 0o000)
        try:
            with pytest.raises(PermissionError) as excinfo:
                list(scope._iter_scoped_sessions(["-repo-through-sealed"], False, roots=[root]))
            assert str(proj_through_sealed) not in str(excinfo.value)
            _assert_scan_error_chain_suppressed(excinfo)
        finally:
            os.chmod(sealed_ancestor, 0o755)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_transcript_records_session_file_gap_but_readable_empty_one_does_not(self, tmp_path):
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        sealed = proj / "sealed.jsonl"
        _write_jsonl(sealed, [_asst("claude-sonnet-4-6", branch="from-sealed")])
        os.chmod(sealed, 0o000)
        (proj / "empty.jsonl").write_text("")
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(sealed, 0o644)

        assert sessions == []  # neither the unreadable nor the readable-empty file yields
        assert scan_gaps == Counter({scope._SCAN_GAP_SESSION_FILE: 1})

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_project_dir_without_search_permission_records_session_file_gap(self, tmp_path):
        """A directory with read but no search (x) permission lists its
        entries, but stat and open on each entry fail with EACCES. That is
        neither an absent entry nor a readable transcript, so it counts as
        a session-file gap and propagates no exception."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-main"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "held.jsonl", [_asst("claude-sonnet-4-6", branch="from-held")])
        os.chmod(proj, 0o444)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj, 0o755)

        assert sessions == []
        assert scan_gaps == Counter({scope._SCAN_GAP_SESSION_FILE: 1})

    def test_nonexistent_root_records_no_gap(self, tmp_path):
        missing_root = tmp_path / "never-created"
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions(
            [missing_root], "*", False, scan_gaps=scan_gaps,
        ))
        assert sessions == []
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_unreadable_project_dir_records_project_dir_gap(self, tmp_path):
        """The structural sibling of the project-dir test above: _iter_scoped_sessions
        selects project dirs by exact slug match rather than glob, but shares
        _iter_project_dir_sessions."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_scoped_sessions(
                ["-repo-open", "-repo-sealed"], False, roots=[root], scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}
        assert scan_gaps == Counter({scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_resolve_project_scope_single_root_glob_branch_raises_on_scan_gaps(self, tmp_path):
        root = tmp_path / "acct-a" / "projects"
        root.mkdir(parents=True)
        args = argparse.Namespace(this_repo=False, projects="*")
        with pytest.raises(ValueError):
            scope._resolve_project_scope(args, "buckets", roots=[root], scan_gaps=Counter())

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_unreadable_project_dir_without_scan_gaps_skips_it_silently(self, tmp_path):
        """Same fixture as the project-dir gap test above, with scan_gaps
        omitted -- a caller passing no counter skips the unreadable
        directory silently, with no exception and the same sessions
        yielded."""
        root = tmp_path / "acct-a"
        proj_open = root / "-repo-open"
        proj_open.mkdir(parents=True)
        _write_jsonl(proj_open / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-open-project")])
        proj_sealed = root / "-repo-sealed"
        proj_sealed.mkdir(parents=True)
        os.chmod(proj_sealed, 0o000)
        try:
            sessions = list(scope._iter_glob_scoped_sessions([root], "*", False))
        finally:
            os.chmod(proj_sealed, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open-project"}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_gap_counter_keys_equal_the_three_level_constants(self, tmp_path):
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-a")])
        proj_c = root_a / "-repo-c"
        proj_c.mkdir(parents=True)
        os.chmod(proj_c, 0o000)
        sealed = proj_a / "sealed.jsonl"
        _write_jsonl(sealed, [_asst("claude-sonnet-4-6", branch="from-sealed")])
        os.chmod(sealed, 0o000)
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        scan_gaps = Counter()
        try:
            list(scope._iter_glob_scoped_sessions([root_a, root_b], "*", False, scan_gaps=scan_gaps))
        finally:
            os.chmod(proj_c, 0o755)
            os.chmod(sealed, 0o644)
            os.chmod(root_b, 0o755)

        assert set(scan_gaps) == {
            scope._SCAN_GAP_ROOT, scope._SCAN_GAP_PROJECT_DIR, scope._SCAN_GAP_SESSION_FILE,
        }

    def test_root_that_is_a_regular_file_records_no_gap(self, tmp_path):
        """Catches a missing NotADirectoryError branch in
        _list_dir_recording_gaps: a scan root that exists as a regular file
        is an empty scope, not a gap."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-a"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-dir-root")])
        root_b = tmp_path / "acct-b-not-a-dir"
        root_b.write_text("not a directory")
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions(
            [root_a, root_b], "*", False, scan_gaps=scan_gaps,
        ))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-dir-root"}
        assert scan_gaps == Counter()

    def test_stray_regular_file_directly_under_root_records_no_gap(self, tmp_path):
        """Asserts _dedup_new_project_dirs' S_ISDIR skip filters a stray file
        (e.g. .DS_Store) before any project-dir listing, independent of the
        NotADirectoryError branch _list_dir_recording_gaps also has."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-a"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-repo-a")])
        (root / ".DS_Store").write_text("")
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-repo-a"}
        assert scan_gaps == Counter()

    def test_directory_dangling_symlink_and_looped_symlink_named_jsonl_record_no_gap(self, tmp_path):
        """Pins _failed_transcript_read_is_gap's classification in both
        directions, paired with the unreadable-transcript test above: a
        directory (stray.jsonl), a dangling symlink (dangling.jsonl), or a
        looped symlink (loop.jsonl) is an empty scope, not a gap, since none
        is a regular file whose read failed."""
        root = tmp_path / "acct-a"
        proj = root / "-repo-a"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "real.jsonl", [_asst("claude-sonnet-4-6", branch="from-real")])
        (proj / "stray.jsonl").mkdir()
        (proj / "dangling.jsonl").symlink_to(proj / "does-not-exist.jsonl")
        (proj / "loop.jsonl").symlink_to(proj / "loop.jsonl")
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions([root], "*", False, scan_gaps=scan_gaps))
        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-real"}
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_multiple_gaps_at_every_level_accumulate_exactly(self, tmp_path):
        """Two gaps at each of the three increment sites tell accumulation
        (+=1) from assignment (=1) apart at every site at once. An
        exact-equality counter check with only one gap per level would
        pass under either bug."""
        root_0 = tmp_path / "acct-sealed-root"
        root_0.mkdir()
        root_1 = tmp_path / "acct-open-root"
        proj_sealed_a = root_1 / "proj-sealed-a"
        proj_sealed_a.mkdir(parents=True)
        proj_sealed_b = root_1 / "proj-sealed-b"
        proj_sealed_b.mkdir(parents=True)
        proj_open = root_1 / "proj-open"
        proj_open.mkdir(parents=True)
        sealed_a = proj_open / "sealed-a.jsonl"
        _write_jsonl(sealed_a, [_asst("claude-sonnet-4-6", branch="from-sealed-a")])
        sealed_b = proj_open / "sealed-b.jsonl"
        _write_jsonl(sealed_b, [_asst("claude-sonnet-4-6", branch="from-sealed-b")])
        _write_jsonl(proj_open / "open.jsonl", [_asst("claude-sonnet-4-6", branch="from-open")])

        os.chmod(root_0, 0o000)
        os.chmod(proj_sealed_a, 0o000)
        os.chmod(proj_sealed_b, 0o000)
        os.chmod(sealed_a, 0o000)
        os.chmod(sealed_b, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_glob_scoped_sessions(
                [root_0, root_1], "*", False, scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_0, 0o755)
            os.chmod(proj_sealed_a, 0o755)
            os.chmod(proj_sealed_b, 0o755)
            os.chmod(sealed_a, 0o644)
            os.chmod(sealed_b, 0o644)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-open"}
        assert scan_gaps == Counter({
            scope._SCAN_GAP_ROOT: 1,
            scope._SCAN_GAP_PROJECT_DIR: 2,
            scope._SCAN_GAP_SESSION_FILE: 2,
        })

    def test_project_dir_symlink_alias_across_roots_records_no_gap_and_yields_once(self, tmp_path):
        """A project dir already visited under another root, reached here
        via symlink, is a candidate _dedup_new_project_dirs drops. It is
        skipped, not recorded as a gap."""
        root_a = tmp_path / "acct-a"
        proj = root_a / "-repo-shared"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="from-shared")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        (root_b / "-repo-shared-alias").symlink_to(proj, target_is_directory=True)
        scan_gaps = Counter()
        sessions = list(scope._iter_glob_scoped_sessions(
            [root_a, root_b], "*", False, scan_gaps=scan_gaps,
        ))
        branches_seen = [rec["gitBranch"] for _jsonl, records in sessions for rec in records]
        assert branches_seen == ["from-shared"]  # exactly once, not twice
        assert scan_gaps == Counter()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_iter_scoped_sessions_records_two_root_level_gaps(self, tmp_path):
        """The count of two distinguishes += 1 from = 1 at _iter_scoped_sessions'
        own root-level except OSError -- the one increment site outside
        _list_dir_recording_gaps and _iter_project_dir_sessions."""
        root_a = tmp_path / "acct-a"
        proj_a = root_a / "-repo-main"
        proj_a.mkdir(parents=True)
        _write_jsonl(proj_a / "sess-a.jsonl", [_asst("claude-sonnet-4-6", branch="from-readable-root")])
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        os.chmod(root_b, 0o000)
        root_c = tmp_path / "acct-c"
        root_c.mkdir()
        os.chmod(root_c, 0o000)
        scan_gaps = Counter()
        try:
            sessions = list(scope._iter_scoped_sessions(
                ["-repo-main"], False, roots=[root_a, root_b, root_c], scan_gaps=scan_gaps,
            ))
        finally:
            os.chmod(root_b, 0o755)
            os.chmod(root_c, 0o755)

        branches_seen = {rec["gitBranch"] for _jsonl, records in sessions for rec in records}
        assert branches_seen == {"from-readable-root"}
        assert scan_gaps == Counter({scope._SCAN_GAP_ROOT: 2})

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ENOTDIR, errno.EBADF, errno.ELOOP])
    def test_stat_mode_unless_absent_returns_none_for_absent_entry_errnos(self, absent_errno):
        """The errno partition is pinned with a stub, not chmod, so it holds
        under root and on every interpreter."""
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        assert scope._stat_mode_unless_absent(_StatRaises()) is None

    @pytest.mark.parametrize("propagated_errno", [errno.EACCES, errno.EIO])
    def test_stat_mode_unless_absent_reraises_every_other_errno(self, propagated_errno):
        class _StatRaises:
            def stat(self):
                raise OSError(propagated_errno, os.strerror(propagated_errno))

        with pytest.raises(OSError) as excinfo:
            scope._stat_mode_unless_absent(_StatRaises())
        assert excinfo.value.errno == propagated_errno

    def test_stat_mode_unless_absent_returns_the_mode_of_an_existing_entry(self, tmp_path):
        regular_file = tmp_path / "regular.jsonl"
        regular_file.write_text("{}\n")
        directory = tmp_path / "project-dir"
        directory.mkdir()

        regular_mode = scope._stat_mode_unless_absent(regular_file)
        directory_mode = scope._stat_mode_unless_absent(directory)

        assert stat.S_ISREG(regular_mode) and not stat.S_ISDIR(regular_mode)
        assert stat.S_ISDIR(directory_mode) and not stat.S_ISREG(directory_mode)

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ELOOP])
    def test_dedup_skips_a_candidate_whose_stat_reports_it_absent_without_a_gap(self, absent_errno):
        """The call-site arm is pinned with a stat stub, not chmod, so it holds under root."""
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        scan_gaps = Counter()
        yielded = list(scope._dedup_new_project_dirs([_StatRaises()], set(), scan_gaps=scan_gaps))
        assert yielded == []
        assert scan_gaps == Counter()

    def test_dedup_records_a_project_dir_gap_when_stat_fails_with_eacces(self):
        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        scan_gaps = Counter()
        yielded = list(scope._dedup_new_project_dirs([_StatRaises()], set(), scan_gaps=scan_gaps))
        assert yielded == []
        assert scan_gaps == Counter({scope._SCAN_GAP_PROJECT_DIR: 1})

    def test_dedup_without_scan_gaps_raises_eacces_with_the_path_stripped(self):
        sensitive_path = "/private/acct-a/projects/-repo-sealed"

        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES), sensitive_path)

        with pytest.raises(OSError) as excinfo:
            list(scope._dedup_new_project_dirs([_StatRaises()], set()))
        assert excinfo.value.errno == errno.EACCES
        assert sensitive_path not in str(excinfo.value)
        _assert_scan_error_chain_suppressed(excinfo)

    @pytest.mark.parametrize("absent_errno", [errno.ENOENT, errno.ELOOP])
    def test_failed_transcript_read_is_not_a_gap_when_stat_reports_it_absent(self, absent_errno):
        class _StatRaises:
            def stat(self):
                raise OSError(absent_errno, os.strerror(absent_errno))

        assert scope._failed_transcript_read_is_gap(_StatRaises()) is False

    def test_failed_transcript_read_is_a_gap_when_stat_fails_with_eacces(self):
        class _StatRaises:
            def stat(self):
                raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        assert scope._failed_transcript_read_is_gap(_StatRaises()) is True

    def test_failed_transcript_read_is_a_gap_when_stat_reports_a_regular_file(self):
        """The regular-file arm, pinned with a stat stub so it holds under root."""
        class _StatsAsRegularFile:
            def stat(self):
                return os.stat_result((stat.S_IFREG | 0o644,) + (0,) * 9)

        assert scope._failed_transcript_read_is_gap(_StatsAsRegularFile()) is True

    def test_failed_transcript_read_is_not_a_gap_when_stat_reports_a_directory(self):
        class _StatsAsDirectory:
            def stat(self):
                return os.stat_result((stat.S_IFDIR | 0o755,) + (0,) * 9)

        assert scope._failed_transcript_read_is_gap(_StatsAsDirectory()) is False


class TestGlobScopedSessionsSelection:
    """_iter_glob_scoped_sessions' own fnmatch selection of project directories
    and *.jsonl transcripts. The class covers the multi-root selection
    engine only; single-root scope uses corpus.iter_sessions (Path.glob),
    which is the reference engine."""

    @pytest.mark.parametrize("projects_glob", ["*", "feat-?", "feat-*", "[ab]*", "zeta", ".hidden*"])
    def test_multi_root_selection_equals_union_of_per_root_path_glob_selection(self, tmp_path, projects_glob):
        project_names_by_root = {
            "acct-a": ["alpha", "feat-a", "feat-bb"],
            "acct-b": ["beta", "feat-c", "zeta", ".hidden-x"],
        }
        roots = []
        for root_name, project_names in project_names_by_root.items():
            root = tmp_path / root_name
            for project_name in project_names:
                project_dir = root / project_name
                project_dir.mkdir(parents=True)
                _write_jsonl(project_dir / "sess.jsonl", [_asst("claude-sonnet-4-6", branch=project_name)])
            roots.append(root)
        stray_records = [_asst("claude-sonnet-4-6", branch="not-a-transcript")]
        backup_copy = roots[0] / "feat-a" / "sess.jsonl.bak"
        non_transcript = roots[0] / "feat-a" / "notes.txt"
        _write_jsonl(backup_copy, stray_records)
        _write_jsonl(non_transcript, stray_records)

        yielded_paths = {
            jsonl for jsonl, _records in scope._iter_glob_scoped_sessions(roots, projects_glob, False)
        }
        every_project_paths = {
            jsonl for jsonl, _records in scope._iter_glob_scoped_sessions(roots, "*", False)
        }

        expected_paths = {
            jsonl for root in roots for jsonl, _records in corpus.iter_sessions(root, projects_glob)
        }
        assert expected_paths
        assert yielded_paths == expected_paths
        if projects_glob != "*":
            assert yielded_paths < every_project_paths
        assert backup_copy not in every_project_paths
        assert non_transcript not in every_project_paths
