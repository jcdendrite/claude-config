import json
import os

import pytest
from transcript_analysis import corpus

from .conftest import _asst, _opus, _user_msg, _write_jsonl, _write_subagent_dispatch, _write_subagent_jsonl


class TestParseTs:
    def test_parse_ts_valid_iso_z(self):
        ts = corpus._parse_ts("2024-06-01T12:00:00.000Z")
        assert ts is not None and ts > 0

    def test_parse_ts_none_input(self):
        assert corpus._parse_ts(None) is None

    def test_parse_ts_invalid_string(self):
        assert corpus._parse_ts("not-a-date") is None


def _read_tool_use(tool_id: str, *, file_path: str, offset: int | None = None, limit: int | None = None) -> dict:
    tool_input: dict = {"file_path": file_path}
    if offset is not None:
        tool_input["offset"] = offset
    if limit is not None:
        tool_input["limit"] = limit
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": tool_input}


def _text_marker_assistant(marker: str, *, sidechain: bool = True) -> dict:
    return _asst("claude-opus-4-7", sidechain=sidechain, content=[{"type": "text", "text": marker}])


def _text_markers(records: list[dict]) -> list[str]:
    return [rec["message"]["content"][0]["text"] for rec in records]


class TestCorpusSubagentDirAndUtf8ErrorHandling:
    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_read_session_file_partitioned_treats_unreadable_subagents_dir_as_absent(self, fake_projects):
        """A subagents/ directory reached through a sealed session-id
        ancestor makes `subagent_dir.is_dir()` raise `PermissionError`.
        (`PermissionError` is an `OSError` subclass.)
        `_read_session_file_partitioned` must treat that the same as an
        absent directory, not crash. The only observable difference from a
        readable-empty directory is that no subagent records are merged."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        _write_subagent_jsonl(fake_projects, "sess", "agent-a", [_opus([_read_tool_use("r2", file_path="/b.py")])])
        session_dir = fake_projects / "sess"
        os.chmod(session_dir, 0o000)
        try:
            result = corpus._read_session_file_partitioned(fake_projects / "sess.jsonl", include_subagents=True)
        finally:
            os.chmod(session_dir, 0o755)

        expected = [[_opus([_read_tool_use("r1", file_path="/a.py")])]]
        assert result == expected

    def test_read_session_file_merges_direct_subagents_but_not_workflow_nested_ones(self, fake_projects):
        """The reader merges only direct `subagents/*.jsonl` children, not `subagents/workflows/wf_*/` ones."""
        _write_jsonl(fake_projects / "sess.jsonl", [_text_marker_assistant("main-record", sidechain=False)])
        _write_subagent_jsonl(fake_projects, "sess", "agent-direct", [_text_marker_assistant("direct-subagent-record")])
        nested_dir = fake_projects / "sess" / corpus.SUBAGENT_SUBDIR / "workflows" / "wf_x"
        nested_dir.mkdir(parents=True)
        _write_jsonl(nested_dir / "agent-nested.jsonl", [_text_marker_assistant("workflow-nested-record")])

        records = corpus.read_session_file(fake_projects / "sess.jsonl", include_subagents=True)

        markers = _text_markers(records)
        assert sorted(markers) == ["direct-subagent-record", "main-record"]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_read_session_file_drops_subagent_files_when_main_transcript_unreadable(self, fake_projects):
        """An unreadable main transcript drops the whole session, including its readable direct subagent files."""

        main_jsonl = fake_projects / "sess.jsonl"
        _write_jsonl(main_jsonl, [_text_marker_assistant("main-record", sidechain=False)])
        _write_subagent_jsonl(fake_projects, "sess", "agent-direct", [_text_marker_assistant("direct-subagent-record")])
        os.chmod(main_jsonl, 0o000)
        try:
            records_while_unreadable = corpus.read_session_file(main_jsonl, include_subagents=True)
        finally:
            os.chmod(main_jsonl, 0o644)
        records_once_readable = corpus.read_session_file(main_jsonl, include_subagents=True)

        assert records_while_unreadable == []
        markers = _text_markers(records_once_readable)
        assert sorted(markers) == ["direct-subagent-record", "main-record"]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_read_session_file_skips_only_the_unreadable_subagent_file(self, fake_projects):
        """An unreadable subagent file drops only itself, keeping readable subagent files on either side."""
        main_jsonl = fake_projects / "sess.jsonl"
        _write_jsonl(main_jsonl, [_text_marker_assistant("main-record", sidechain=False)])
        _write_subagent_jsonl(fake_projects, "sess", "agent-readable", [_text_marker_assistant("readable-subagent-record")])
        _write_subagent_jsonl(fake_projects, "sess", "agent-sealed", [_text_marker_assistant("sealed-subagent-record")])
        _write_subagent_jsonl(fake_projects, "sess", "agent-z-later", [_text_marker_assistant("later-subagent-record")])
        sealed_jsonl = fake_projects / "sess" / corpus.SUBAGENT_SUBDIR / "agent-sealed.jsonl"
        os.chmod(sealed_jsonl, 0o000)
        try:
            records = corpus.read_session_file(main_jsonl, include_subagents=True)
        finally:
            os.chmod(sealed_jsonl, 0o644)

        markers = _text_markers(records)
        assert sorted(markers) == ["later-subagent-record", "main-record", "readable-subagent-record"]

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_index_subagent_dispatches_returns_empty_index_for_unreadable_subagents_dir(self, fake_projects):
        """A subagents/ directory reached through a sealed session-id
        ancestor makes subagent_dir.is_dir() raise PermissionError (an
        OSError) -- corpus._index_subagent_dispatches must return an empty
        index and no read errors, not crash, so every dispatch in that
        session reads as dangling. Mirrors
        test_read_session_file_partitioned_treats_unreadable_subagents_dir_as_absent's
        chmod-based direct-call pattern for the sibling function."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        _write_subagent_dispatch(
            fake_projects, "sess", "agent-a", "toolu_1",
            [_opus([_read_tool_use("r2", file_path="/b.py")])],
        )
        session_dir = fake_projects / "sess"
        os.chmod(session_dir, 0o000)
        try:
            index, meta_read_errors = corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")
        finally:
            os.chmod(session_dir, 0o755)

        assert index == {}
        assert meta_read_errors == 0

    def test_index_subagent_dispatches_counts_non_utf8_meta_as_read_error(self, fake_projects):
        """A meta.json containing non-UTF-8 bytes must be counted under
        meta_read_errors, identical to an invalid-JSON or unreadable
        meta.json -- corpus._index_subagent_dispatches must not raise
        UnicodeDecodeError uncaught and abort the whole run. Uses the same
        non-UTF-8 bytes as
        test_parse_jsonl_records_returns_empty_list_for_a_file_of_only_non_utf8_bytes."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        subdir = fake_projects / "sess" / corpus.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-1.meta.json").write_bytes(b"\xff\xfe\x00\x01")

        index, meta_read_errors = corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")

        assert index == {}
        assert meta_read_errors == 1

    def test_parse_jsonl_records_skips_a_non_utf8_line_and_keeps_the_valid_ones(self, tmp_path):
        """A line with non-UTF-8 bytes is skipped on its own like a
        malformed-JSON line, so the valid records before and after it
        survive instead of the whole file being discarded."""
        mixed_jsonl = tmp_path / "mixed.jsonl"
        mixed_jsonl.write_bytes(
            b'{"type": "user", "n": 1}\n\xff\xfe\x00\x01\n{"type": "user", "n": 2}\n'
        )
        assert corpus._parse_jsonl_records(mixed_jsonl) == [
            {"type": "user", "n": 1}, {"type": "user", "n": 2},
        ]

    def test_parse_jsonl_records_returns_empty_list_for_a_file_of_only_non_utf8_bytes(self, tmp_path):
        """A readable file with no decodable record is empty ([]), not
        unreadable (None), so an all-corrupt transcript is not mistaken for
        a failed read."""
        bad_jsonl = tmp_path / "bad.jsonl"
        bad_jsonl.write_bytes(b"\xff\xfe\x00\x01")
        assert corpus._parse_jsonl_records(bad_jsonl) == []

    def test_parse_jsonl_records_returns_none_when_the_file_cannot_be_opened(self, tmp_path):
        assert corpus._parse_jsonl_records(tmp_path / "missing.jsonl") is None

    def test_parse_jsonl_records_keeps_valid_multibyte_utf8_lines_on_both_sides_of_a_corrupt_line(self, tmp_path):
        """A transcript written as raw UTF-8 (not ASCII-escaped, unlike
        _write_jsonl's output) round-trips every record intact. The
        assertion compares whole records, so a codec that cannot decode
        the multi-byte characters, or decodes them wrongly, fails it."""
        multibyte_text = "café 🎉 — 北京"
        multibyte_jsonl = tmp_path / "multibyte.jsonl"
        multibyte_jsonl.write_bytes(
            json.dumps({"type": "user", "n": 1, "text": multibyte_text}, ensure_ascii=False).encode("utf-8")
            + b"\n\xff\xfe\x00\x01\n"
            + json.dumps({"type": "user", "n": 2, "text": multibyte_text}, ensure_ascii=False).encode("utf-8")
            + b"\n"
        )
        assert corpus._parse_jsonl_records(multibyte_jsonl) == [
            {"type": "user", "n": 1, "text": multibyte_text},
            {"type": "user", "n": 2, "text": multibyte_text},
        ]

    def test_parse_jsonl_records_drops_a_record_with_a_truncated_multibyte_character_and_keeps_its_neighbours(
        self, tmp_path
    ):
        """An invalid byte inside an otherwise valid record's JSON string
        drops that whole record rather than keeping it with a replacement
        character, so a lossy decode (errors="replace") fails this test."""
        truncated_multibyte_jsonl = tmp_path / "truncated.jsonl"
        truncated_multibyte_jsonl.write_bytes(
            b'{"type": "user", "n": 1}\n'
            + b'{"type": "user", "n": 2, "text": "caf\xc3"}\n'
            + b'{"type": "user", "n": 3}\n'
        )
        assert corpus._parse_jsonl_records(truncated_multibyte_jsonl) == [
            {"type": "user", "n": 1}, {"type": "user", "n": 3},
        ]

    def test_index_subagent_dispatches_reads_a_multibyte_utf8_meta_json_without_a_read_error(self, fake_projects):
        """A meta.json written as raw UTF-8 with non-ASCII text next to a
        valid toolUseId is indexed, not counted under meta_read_errors."""
        _write_jsonl(fake_projects / "sess.jsonl", [_opus([_read_tool_use("r1", file_path="/a.py")])])
        subdir = fake_projects / "sess" / corpus.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        meta = {"agentType": "reviewer", "description": "café 🎉 — 北京", "toolUseId": "toolu_1", "spawnDepth": 1}
        (subdir / "agent-1.meta.json").write_bytes(json.dumps(meta, ensure_ascii=False).encode("utf-8"))

        index, meta_read_errors = corpus._index_subagent_dispatches(fake_projects / "sess.jsonl")

        assert index == {"toolu_1": (subdir / "agent-1.jsonl", None)}
        assert meta_read_errors == 0


class TestIterSessionsOrdering:
    """iter_sessions must yield in a single flat sort over full file paths, NOT
    grouped by directory. cmd_audit_routing's redact-label first pass assigns
    Project-N labels by first-seen order, and this repo's own worktree dirs
    (-home-u-repo vs -home-u-repo--claude-worktrees-b) are the prefix-colliding
    pair whose flat-sort vs dir-grouped order differs."""

    def test_flat_path_order_across_prefix_colliding_dirs(self, tmp_path):
        projects = tmp_path / "projects"
        main_dir = projects / "-home-u-repo"
        wt_dir = projects / "-home-u-repo--claude-worktrees-b"
        main_dir.mkdir(parents=True)
        wt_dir.mkdir(parents=True)
        _write_jsonl(main_dir / "a.jsonl", [_asst("claude-sonnet-4-6", branch="main")])
        _write_jsonl(wt_dir / "m.jsonl", [_asst("claude-sonnet-4-6", branch="b")])
        yielded = [jsonl for jsonl, _records in corpus.iter_sessions(projects, "*")]
        # The flat full-path sort places the worktree dir's file first ('-' 0x2D <
        # '/' 0x2F at the byte after the shared prefix); a dir-grouped traversal
        # would place the main dir first. Pin the flat order explicitly.
        expected = sorted(projects.glob("*/*.jsonl"))
        assert yielded == expected, (
            f"iter_sessions must yield in flat full-path sort order; got "
            f"{[p.parent.name + '/' + p.name for p in yielded]}"
        )


class TestIterSessionsSubagentMerge:
    def test_include_subagents_false_ignores_subdir(self, fake_projects):
        """include_subagents=False must not read the subagents/ subdirectory."""
        session_id = "sess-abc"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=False))
        assert len(results) == 1
        _path, records = results[0]
        # Only the main record; sidechain record must not appear.
        assert len(records) == 1
        assert records[0].get("isSidechain") is False

    def test_include_subagents_true_merges_subagent_records(self, fake_projects):
        """include_subagents=True appends subagent file records to the session's record list."""
        session_id = "sess-def"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 2
        types = [r.get("isSidechain") for r in records]
        assert False in types
        assert True in types

    def test_missing_subagent_dir_is_safe(self, fake_projects):
        """A session with no subagents/ directory yields normally with include_subagents=True."""
        _write_jsonl(fake_projects / "lone-session.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # No subagents/ dir for this session.
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 1

    def test_multiple_subagent_files_all_merged(self, fake_projects):
        """All *.jsonl files in the subagents/ dir are merged into one record list."""
        session_id = "sess-multi"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", sidechain=True),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-2", [
            _asst("claude-haiku-4-5", branch="main", sidechain=True),
        ])
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        assert len(records) == 3, "expected main + 2 subagent records"
        models = [(r.get("message") or {}).get("model") for r in records]
        assert "claude-opus-4-7" in models
        assert "claude-sonnet-4-6" in models
        assert "claude-haiku-4-5" in models

    def test_corrupt_line_in_subagent_file_skipped_gracefully(self, fake_projects):
        """A corrupt JSON line in a subagent file is skipped; valid records still merge."""
        session_id = "sess-corrupt"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="main"),
        ])
        # Write a subagent file with one valid record and one corrupt line.
        subdir = fake_projects / session_id / corpus.SUBAGENT_SUBDIR
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "agent-corrupt.jsonl").write_text(
            '{"type":"assistant","isSidechain":true,"gitBranch":"main",'
            '"message":{"model":"claude-sonnet-4-6","content":[],"usage":{}}}\n'
            'THIS IS NOT JSON\n'
        )
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain = [r for r in records if r.get("isSidechain")]
        assert len(sidechain) == 1, "corrupt line skipped; valid sidechain record present"


class TestSubagentFormatContract:
    """Subagent transcript record shape as read back through corpus.iter_sessions.

    None of these tests can detect drift in the real on-disk transcript format.
    The field assertions in test_subagent_assistant_record_carries_required_fields
    read back values the fixture helpers wrote. Only its sidechain-record count
    depends on production code, the subagent-file merge in iter_sessions.
    The other two tests check the fixture helpers against corpus.SUBAGENT_SUBDIR
    and the record shape the helpers emit.
    The literal "subagents" directory name is pinned independently by
    test_transcript_analysis_subagents_subprocess_finds_seeded_sidechain and
    test_transcript_analysis_git_and_sessions_subprocess_finds_seeded_session
    in test_transcript_cli_bootstrap.py.
    """

    def test_subagent_dir_path_convention(self, fake_projects):
        """_write_subagent_jsonl places files at <session_id>/corpus.SUBAGENT_SUBDIR/*.jsonl.

        Checks the fixture helper against the constant, so it catches the two
        diverging, not a change in the real on-disk layout."""
        session_id = "abc123"
        agent_id = "agent-xyz"
        _write_subagent_jsonl(
            fake_projects, session_id, agent_id,
            [_asst("claude-sonnet-4-6", sidechain=True)]
        )
        subdir = fake_projects / session_id / corpus.SUBAGENT_SUBDIR
        assert subdir.is_dir(), f"Expected subagent dir at {subdir}"
        files = list(subdir.glob("*.jsonl"))
        assert len(files) == 1

    def test_subagent_assistant_record_carries_required_fields(self, fake_projects):
        """Assistant-type subagent records carry isSidechain, gitBranch, type, message.model
        when read back through iter_sessions with include_subagents=True."""
        session_id = "contract-sess"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-opus-4-7", branch="feat"),
        ])
        _write_subagent_jsonl(
            fake_projects, session_id, "agent-contract",
            [_asst("claude-sonnet-4-6", branch="feat", sidechain=True)]
        )
        results = list(corpus.iter_sessions(fake_projects.parent, include_subagents=True))
        assert len(results) == 1
        _path, records = results[0]
        sidechain_records = [r for r in records if r.get("isSidechain")]
        assert len(sidechain_records) == 1, "expected one sidechain record from subagent file"
        rec = sidechain_records[0]
        assert rec.get("isSidechain") is True
        assert rec.get("gitBranch") == "feat"
        assert rec.get("type") == "assistant"
        assert (rec.get("message") or {}).get("model") == "claude-sonnet-4-6"

    def test_user_msg_helper_has_no_message_model(self):
        """User-type records from the _user_msg() test helper have no message.model.

        Tests the helper's shape only, not iter_sessions or any production code.
        """
        rec = _user_msg("some content", branch="feat")
        # isSidechain may be absent on user records; message.model must not be asserted on them
        assert rec.get("type") == "user"
        assert (rec.get("message") or {}).get("model") is None


class TestProjectsGlobStepsToParent:
    @pytest.mark.parametrize("projects_glob", ["..", "../x", "x/..", "a/../.."])
    def test_dotdot_path_component_steps_to_parent(self, projects_glob):
        assert corpus._projects_glob_steps_to_parent(projects_glob) is True

    @pytest.mark.parametrize("projects_glob", ["a..b", "..*", "...", ".hidden*", "*"])
    def test_dotdot_inside_a_component_does_not_step_to_parent(self, projects_glob):
        assert corpus._projects_glob_steps_to_parent(projects_glob) is False
