"""Tests for analyze-context.py's CLAUDE_DIR resolution."""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "analyze-context.py"
_spec = importlib.util.spec_from_file_location("analyze_context", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def test_claude_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """CLAUDE_DIR (and PROJECTS_DIR/SESSION_META_DIR derived from it) is
    computed at import time from config_dir(); a fresh import with
    CLAUDE_CONFIG_DIR set resolves under that directory instead of
    ~/.claude."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("analyze_context_config_dir_case", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tmp_path == mod.CLAUDE_DIR
    assert tmp_path / "projects" == mod.PROJECTS_DIR
    assert tmp_path / "usage-data" / "session-meta" == mod.SESSION_META_DIR


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _asst_record(**usage) -> dict:
    return {"type": "assistant", "message": {"model": "claude-opus-4-7", "usage": usage}}


def _write_session_meta(config_dir: Path, session_id: str, project_path: str, tokens: int) -> None:
    meta_dir = config_dir / "usage-data" / "session-meta"
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id,
        "project_path": project_path,
        "input_tokens": tokens,
        "output_tokens": 0,
    }))


class TestFindSessionJsonlMultiRoot:
    """find_session_jsonl searches roots active-profile-first and must warn,
    not resolve silently, when the same id prefix matches different sessions
    under different declared roots (plan Step 15, bullet 1)."""

    def test_unambiguous_prefix_found_only_under_a_declared_root_needs_no_warning(
        self, tmp_path, capsys
    ):
        active = tmp_path / "active" / "projects"
        declared = tmp_path / "declared" / "projects"
        proj = declared / "-home-user-repo"
        proj.mkdir(parents=True)
        _write_jsonl(proj / "abc123-only-here.jsonl", [_asst_record(input_tokens=1)])

        result = _mod.find_session_jsonl("abc123", roots=[active, declared])

        assert result == proj / "abc123-only-here.jsonl"
        assert "warning" not in capsys.readouterr().err.lower()

    def test_same_prefix_resolving_to_different_sessions_across_roots_warns_and_uses_active(
        self, tmp_path, capsys
    ):
        active = tmp_path / "active" / "projects"
        proj_active = active / "-home-user-repo-a"
        proj_active.mkdir(parents=True)
        active_match = proj_active / "abc123-active.jsonl"
        _write_jsonl(active_match, [_asst_record(input_tokens=1)])

        declared = tmp_path / "declared" / "projects"
        proj_declared = declared / "-home-user-repo-b"
        proj_declared.mkdir(parents=True)
        _write_jsonl(proj_declared / "abc123-declared.jsonl", [_asst_record(input_tokens=1)])

        result = _mod.find_session_jsonl("abc123", roots=[active, declared])

        assert result == active_match  # active profile wins first-match
        err = capsys.readouterr().err
        assert "abc123" in err
        assert "matches different sessions" in err


class TestLatestSessionJsonlMultiRoot:
    """latest_session_jsonl compares mtimes across every root -- the same
    cwd's project dir can exist under more than one declared root (plan Step
    15, bullet 2)."""

    def test_newer_session_under_a_declared_root_wins_over_older_active_session(
        self, tmp_path
    ):
        project_key = "-home-user-repo"
        active = tmp_path / "active" / "projects"
        proj_active = active / project_key
        proj_active.mkdir(parents=True)
        older = proj_active / "older.jsonl"
        _write_jsonl(older, [_asst_record(input_tokens=1)])
        old_time = time.time() - 3600
        os.utime(older, (old_time, old_time))

        declared = tmp_path / "declared" / "projects"
        proj_declared = declared / project_key
        proj_declared.mkdir(parents=True)
        newer = proj_declared / "newer.jsonl"
        _write_jsonl(newer, [_asst_record(input_tokens=1)])

        result = _mod.latest_session_jsonl(project_key, roots=[active, declared])

        assert result == (newer.stem, newer)


class TestShowTopMultiRootMetadataPairing:
    """show_top reads each root's own usage-data/session-meta dir -- metadata
    must pair with the root a session's transcript actually came from, not
    always the active profile's SESSION_META_DIR (plan Step 15, bullet 3)."""

    def _write_meta(self, config_dir: Path, session_id: str, project_path: str, tokens: int) -> None:
        meta_dir = config_dir / "usage-data" / "session-meta"
        meta_dir.mkdir(parents=True, exist_ok=True)
        (meta_dir / f"{session_id}.json").write_text(json.dumps({
            "session_id": session_id,
            "project_path": project_path,
            "input_tokens": tokens,
            "output_tokens": 0,
        }))

    def test_declared_root_sessions_are_reported_from_their_own_meta_dir(self, tmp_path, capsys):
        active_config = tmp_path / "active"
        self._write_meta(active_config, "session-active", "/repos/repo-active", 100)

        declared_config = tmp_path / "declared"
        self._write_meta(declared_config, "session-declared", "/repos/repo-declared", 200)

        roots = [active_config / "projects", declared_config / "projects"]
        _mod.show_top(10, roots)

        out = capsys.readouterr().out
        # Check each project's own line carries its own token count, not the
        # other root's -- proves metadata paired with the right root, not
        # just that both roots were scanned.
        active_line = next(ln for ln in out.splitlines() if "repo-active" in ln)
        declared_line = next(ln for ln in out.splitlines() if "repo-declared" in ln)
        # Match the direct_tokens column's own format spec, not a bare
        # substring -- a session id or date containing "100"/"200" would
        # false-positive a plain `in` check.
        assert f"{100:>12,}" in active_line
        assert f"{200:>12,}" in declared_line


class TestScopeDisclosureHeader:
    """show_top and analyze_session both print the unconditional
    resolved-scope header transcript-analysis.py's other subcommands print --
    a single-vs-multi-account scan must not read identically in output
    (ciso-reviewer Finding A)."""

    def test_show_top_states_single_root_when_nothing_declared(self, tmp_path, capsys):
        active_config = tmp_path / "active"
        (active_config / "usage-data" / "session-meta").mkdir(parents=True)

        _mod.show_top(10, [active_config / "projects"])

        out = capsys.readouterr().out
        assert out.splitlines()[0] == (
            "TOP SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )

    def test_show_top_states_root_count_at_multi_root(self, tmp_path, capsys):
        active_config = tmp_path / "active"
        declared_config = tmp_path / "declared"
        (active_config / "usage-data" / "session-meta").mkdir(parents=True)
        (declared_config / "usage-data" / "session-meta").mkdir(parents=True)

        _mod.show_top(10, [active_config / "projects", declared_config / "projects"])

        out = capsys.readouterr().out
        assert out.splitlines()[0] == "TOP SOURCES (*; 2 roots)"

    def test_show_top_not_found_states_scanned_root_count_not_a_path(self, tmp_path, capsys):
        """The not-found message (Finding D) states how many roots were
        searched, not any individual root's path -- same disclosure
        discipline as the header itself."""
        roots = [tmp_path / "a" / "projects", tmp_path / "b" / "projects", tmp_path / "c" / "projects"]

        with pytest.raises(SystemExit):
            _mod.show_top(10, roots)

        err = capsys.readouterr().err
        assert "not found in any of 3 scanned roots" in err
        assert str(tmp_path) not in err

    def test_analyze_session_states_single_root_when_nothing_declared(self, tmp_path, capsys):
        jsonl = tmp_path / "sess.jsonl"
        _write_jsonl(jsonl, [_asst_record(input_tokens=1)])

        _mod.analyze_session("sess", jsonl, [tmp_path / "active" / "projects"])

        out = capsys.readouterr().out
        assert out.splitlines()[0] == (
            "SESSION SOURCES (sess; 1 root (no ~/.claude/transcript-config-dirs declared))"
        )

    def test_analyze_session_states_root_count_at_multi_root(self, tmp_path, capsys):
        jsonl = tmp_path / "sess.jsonl"
        _write_jsonl(jsonl, [_asst_record(input_tokens=1)])
        roots = [tmp_path / "active" / "projects", tmp_path / "declared" / "projects"]

        _mod.analyze_session("sess", jsonl, roots)

        out = capsys.readouterr().out
        assert out.splitlines()[0] == "SESSION SOURCES (sess; 2 roots)"


class TestParseTurnsIncludesSubagents:
    """parse_turns routes through _read_session_file(include_subagents=True)
    so a session that dispatched work to a subagent contributes that
    subagent's turns to the growth curve instead of silently omitting them
    (plan Step 17 / staff-backend-engineer Finding B)."""

    def test_subagent_turns_are_merged_into_the_growth_curve(self, tmp_path):
        session_id = "sess-with-subagent"
        jsonl = tmp_path / f"{session_id}.jsonl"
        _write_jsonl(jsonl, [_asst_record(input_tokens=100)])

        subagent_dir = tmp_path / session_id / "subagents"
        subagent_dir.mkdir(parents=True)
        _write_jsonl(subagent_dir / "sub1.jsonl", [_asst_record(input_tokens=500)])

        turns = _mod.parse_turns(jsonl)

        assert [t["total_in"] for t in turns] == [100, 500]

    def test_unparseable_lines_are_skipped_without_raising(self, tmp_path):
        jsonl = tmp_path / "sess-with-bad-line.jsonl"
        jsonl.write_text(json.dumps(_asst_record(input_tokens=1)) + "\nnot json\n")

        turns = _mod.parse_turns(jsonl)

        assert [t["total_in"] for t in turns] == [1]


def test_main_scans_every_declared_root(monkeypatch, tmp_path, capsys):
    """main() threads _resolve_scan_roots' output into show_top -- a session
    metadata file under a declared (non-active) root must appear in the
    CLI's own --top output, not just be reachable via a direct
    show_top(roots=...) call (plan Step 15 / staff-backend-engineer
    Finding E)."""
    active_config = tmp_path / "active"
    _write_session_meta(active_config, "session-active", "/repos/active-proj", 100)
    monkeypatch.setattr(_mod._transcript_analysis, "PROJECTS_DIR", active_config / "projects")

    declared_config = tmp_path / "declared-account"
    (declared_config / "projects").mkdir(parents=True)
    _write_session_meta(declared_config, "session-declared", "/repos/declared-proj", 200)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_config}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

    monkeypatch.setattr(sys, "argv", ["analyze-context.py", "--top"])
    _mod.main()

    out = capsys.readouterr().out
    assert "active-proj" in out
    assert "declared-proj" in out
