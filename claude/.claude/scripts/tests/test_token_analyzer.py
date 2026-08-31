"""Smoke tests for token-analyzer.py."""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

import pytest

from .conftest import _write_subagent_jsonl

# Load token-analyzer as a module without it being on sys.path as a package.
_SCRIPT = Path(__file__).parent.parent / "token-analyzer.py"
_spec = importlib.util.spec_from_file_location("token_analyzer", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
sys.path.insert(0, str(_SCRIPT.parent))
_spec.loader.exec_module(_mod)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _make_assistant(
    model: str,
    inp: int,
    out: int,
    cc: int,
    cr: int,
    tool_names: list[str] | None = None,
    thinking: bool = False,
    skill_name: str | None = None,
    task: bool = False,
    sidechain: bool = False,
    timestamp: str | None = None,
    request_id: str | None = None,
) -> dict:
    content = [{"type": "tool_use", "name": n} for n in (tool_names or [])]
    if thinking:
        content.append({"type": "thinking", "thinking": "..."})
    if skill_name is not None:
        content.append({"type": "tool_use", "name": "Skill", "input": {"skill": skill_name}})
    if task:
        content.append({"type": "tool_use", "name": "Task"})
    rec: dict = {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "model": model,
            "usage": {
                "input_tokens": inp,
                "output_tokens": out,
                "cache_creation_input_tokens": cc,
                "cache_read_input_tokens": cr,
            },
            "content": content,
        },
    }
    if timestamp is not None:
        rec["timestamp"] = timestamp
    if request_id is not None:
        rec["requestId"] = request_id
    return rec


def _make_user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def _make_session(
    session_id: str,
    proj: str = "proj",
    fam: str = "opus",
    out: int = 0,
    inp: int = 0,
    plan: bool = False,
    edits: bool = False,
    task: bool = False,
    thinking: bool = False,
    judgment_skill: bool = False,
    sidechain: bool = False,
) -> dict:
    """Build a session dict matching _walk()'s real per-session output shape,
    for tests that exercise _render_report directly without going through
    _walk (and its jsonl-fixture setup) first."""
    return {
        "id": session_id, "proj": proj, "fam": fam, "out": out, "inp": inp,
        "plan": plan, "edits": edits, "task": task, "thinking": thinking,
        "judgment_skill": judgment_skill, "sidechain": sidechain,
    }


def _iso(offset_seconds: float = 0) -> str:
    """Return an ISO 8601 UTC timestamp offset from now by offset_seconds (negative = past)."""
    from datetime import UTC, datetime
    return datetime.fromtimestamp(time.time() + offset_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch):
    """Local, not the conftest.py fixture of the same name: _walk()'s default
    root reads this file's own module-level PROJECTS_DIR, not scope.PROJECTS_DIR,
    so this must patch _mod.PROJECTS_DIR directly rather than merge into the
    shared fixture's scope.PROJECTS_DIR/config_dir patches."""
    projects = tmp_path / "projects"
    proj_a = projects / "-home-user-repo"
    proj_b = projects / "-home-user-other"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
    return proj_a, proj_b


def test_projects_dir_honors_claude_config_dir(monkeypatch, tmp_path):
    """PROJECTS_DIR is computed at import time from config_dir(); a fresh
    import with CLAUDE_CONFIG_DIR set resolves under that directory instead
    of ~/.claude."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    spec = importlib.util.spec_from_file_location("token_analyzer_config_dir_case", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert tmp_path / "projects" == mod.PROJECTS_DIR


def test_per_model_totals(fake_projects):
    session_a, session_b = fake_projects
    _write_jsonl(
        session_a / "sess1.jsonl",
        [
            _make_user("Plan mode is active"),
            _make_assistant("claude-opus-4-7", inp=100, out=200, cc=50, cr=300),
        ],
    )
    _write_jsonl(
        session_b / "sess2.jsonl",
        [_make_assistant("claude-sonnet-4-6", inp=10, out=20, cc=5, cr=30)],
    )

    ft, sessions = _mod._walk()

    assert ft["opus"]["inp"] == 100
    assert ft["opus"]["out"] == 200
    assert ft["opus"]["cc"] == 50
    assert ft["opus"]["cr"] == 300
    assert ft["sonnet"]["inp"] == 10
    assert ft["sonnet"]["out"] == 20
    assert len(sessions) == 2


def test_per_model_totals_mixed_session(fake_projects):
    """Mixed-model session credits each record's tokens to its own family."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "mixed.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=100, out=200, cc=0, cr=0),
            _make_assistant("claude-sonnet-4-6", inp=10, out=30, cc=0, cr=0),
        ],
    )

    ft, sessions = _mod._walk()

    assert ft["opus"]["out"] == 200
    assert ft["sonnet"]["out"] == 30
    assert ft["opus"]["n"] == 1
    assert ft["sonnet"]["n"] == 1
    assert len(sessions) == 1


def test_multi_block_request_id_run_counted_once(fake_projects):
    """Three same-requestId records collapse to one turn; ascending output_tokens
    per block also pins that usage is read from the run's last record, not
    summed or taken from the first."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "multi_block.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=1000, out=50, cc=200, cr=300, request_id="req-1"),
            _make_assistant("claude-opus-4-7", inp=1000, out=140, cc=200, cr=300, request_id="req-1"),
            _make_assistant("claude-opus-4-7", inp=1000, out=200, cc=200, cr=300, request_id="req-1"),
        ],
    )

    ft, sessions = _mod._walk()

    assert ft["opus"]["out"] == 200   # last block's value, not 50+140+200=390
    assert ft["opus"]["inp"] == 1000  # shared value, not summed 3x
    assert ft["opus"]["cc"] == 200
    assert ft["opus"]["cr"] == 300
    assert len(sessions) == 1
    assert sessions[0]["out"] == 200


def test_multi_block_request_id_run_since_windowed_by_first_block(fake_projects):
    """--since windowing on a merged run uses the first block's timestamp (see
    _merge_assistant_run), so a run straddling the cutoff is excluded entirely
    rather than partially counted."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "straddling_run.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=100, out=50, cc=0, cr=0,
                            request_id="req-1", timestamp=_iso(-5 * 86400)),  # before cutoff
            _make_assistant("claude-opus-4-7", inp=100, out=200, cc=0, cr=0,
                            request_id="req-1", timestamp=_iso(-1 * 3600)),   # after cutoff
        ],
    )

    cutoff = time.time() - 2 * 86400
    ft, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 0
    assert "opus" not in ft


def test_subagent_multi_block_request_id_run_credited_once(fake_projects):
    """A multi-block, same-requestId run entirely inside a subagent split
    file is credited once per family, not once per block -- extends
    test_walk_credits_subagent_dispatched_cache_tokens's single-block
    coverage to a merged multi-block run."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "with-subagent-multi.jsonl",
        [_make_assistant("claude-opus-4-7", inp=10, out=50, cc=0, cr=100)],
    )
    _write_subagent_jsonl(
        session_a, "with-subagent-multi", "agent1",
        [
            _make_assistant("claude-opus-4-7", inp=5, out=20, cc=0, cr=500,
                            sidechain=True, request_id="req-side"),
            _make_assistant("claude-opus-4-7", inp=5, out=60, cc=0, cr=500,
                            sidechain=True, request_id="req-side"),
        ],
    )

    ft, sessions = _mod._walk()

    assert ft["opus"]["cr"] == 600       # 100 (main) + 500 (subagent run once, not 1000)
    assert ft["opus"]["out"] == 50 + 60  # main's 50 + subagent run's last-block value, not 50+20+60
    assert sessions[0]["sidechain"] is True


def test_cache_hit_ratio():
    # cache_read / (cache_read + input) per spec
    assert _mod._pct(300, 300 + 100) == "75%"
    assert _mod._pct(0, 0) == "—"


def test_candidate_flagging(fake_projects):
    session_a, session_b = fake_projects
    # Session A: Opus + plan mode → NOT a candidate
    _write_jsonl(
        session_a / "with_plan.jsonl",
        [
            _make_user("Plan mode is active in this session"),
            _make_assistant("claude-opus-4-7", inp=100, out=1000, cc=0, cr=0),
        ],
    )
    # Session B: Opus + no plan + no edits → IS a candidate (output ≥ 500)
    _write_jsonl(
        session_b / "no_plan.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=800, cc=0, cr=0)],
    )

    _, sessions = _mod._walk()
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    cands = [s for s in sessions if s["fam"] == "opus" and not any(s[k] for k in _excl) and s["out"] >= 500]
    non_cands = [s for s in sessions if s["plan"]]

    assert len(cands) == 1, f"expected 1 candidate, got {cands}"
    assert len(non_cands) == 1
    assert cands[0]["out"] == 800


def test_edit_tool_excludes_candidate(fake_projects):
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "with_edit.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, tool_names=["Edit"])],
    )

    _, sessions = _mod._walk()
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    cands = [s for s in sessions if s["fam"] == "opus" and not any(s[k] for k in _excl) and s["out"] >= 500]
    assert len(cands) == 0


def test_candidate_excludes_task(fake_projects):
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "with_task.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, task=True)],
    )

    _, sessions = _mod._walk()
    assert sessions[0]["task"] is True
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    cands = [s for s in sessions if s["fam"] == "opus" and not any(s[k] for k in _excl) and s["out"] >= 500]
    assert len(cands) == 0


def test_candidate_excludes_thinking(fake_projects):
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "with_thinking.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, thinking=True)],
    )

    _, sessions = _mod._walk()
    assert sessions[0]["thinking"] is True
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    cands = [s for s in sessions if s["fam"] == "opus" and not any(s[k] for k in _excl) and s["out"] >= 500]
    assert len(cands) == 0


def test_candidate_excludes_judgment_skill(fake_projects):
    """Judgment skill invocation excludes; non-judgment Skill invocation does not."""
    session_a, session_b = fake_projects
    _write_jsonl(
        session_a / "with_code_review.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, skill_name="code-review")],
    )
    _write_jsonl(
        session_b / "with_cleanup.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, skill_name="cleanup-merged-branch")],
    )

    _, sessions = _mod._walk()
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    cands = [s for s in sessions if s["fam"] == "opus" and not any(s[k] for k in _excl) and s["out"] >= 500]
    excluded = [s for s in sessions if s["judgment_skill"]]

    assert len(excluded) == 1
    assert len(cands) == 1
    assert cands[0]["judgment_skill"] is False


def test_sidechain_excluded_from_session_fam(fake_projects):
    """Sidechain records don't shift the reported dominant family."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "mixed_side.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=50, out=300, cc=0, cr=0),
            _make_assistant("claude-sonnet-4-6", inp=200, out=800, cc=0, cr=0, sidechain=True),
        ],
    )

    ft, sessions = _mod._walk()

    assert len(sessions) == 1
    assert sessions[0]["fam"] == "opus"   # dominant non-sidechain family
    assert ft["opus"]["out"] == 300
    assert ft["sonnet"]["out"] == 800     # sidechain tokens still credited correctly
    assert sessions[0]["sidechain"] is True


def test_sidechain_edits_do_not_disqualify_parent(fake_projects):
    """Edit in a sidechain record does not set had_edits for the parent session."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "parent_side_edit.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0),
            _make_assistant("claude-sonnet-4-6", inp=20, out=100, cc=0, cr=0,
                            tool_names=["Edit"], sidechain=True),
        ],
    )

    _, sessions = _mod._walk()
    assert sessions[0]["edits"] is False
    _excl = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    # sidechain=True means excluded anyway, but edits flag must be False
    assert sessions[0]["sidechain"] is True
    assert sessions[0]["edits"] is False


def test_since_filter(fake_projects):
    session_a, session_b = fake_projects
    old_file = session_a / "old_sess.jsonl"
    new_file = session_b / "new_sess.jsonl"

    _write_jsonl(old_file, [_make_assistant("claude-opus-4-7", inp=10, out=100, cc=0, cr=0,
                                            timestamp=_iso(-10 * 86400))])
    _write_jsonl(new_file, [_make_assistant("claude-sonnet-4-6", inp=10, out=200, cc=0, cr=0,
                                            timestamp=_iso(-1 * 3600))])

    # Back-date old file mtime so the mtime pre-filter also excludes it
    old_mtime = time.time() - 10 * 86400
    os.utime(old_file, (old_mtime, old_mtime))

    cutoff = time.time() - 2 * 86400
    ft, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 1
    assert sessions[0]["fam"] == "sonnet"
    assert ft.get("opus") is None or ft["opus"]["n"] == 0


def test_per_record_window_filter(fake_projects):
    """Session with records spanning the cutoff: only in-window tokens are counted."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "straddling.jsonl",
        [
            _make_assistant("claude-opus-4-7", inp=100, out=400, cc=0, cr=0,
                            timestamp=_iso(-5 * 86400)),   # 5 days ago — out of window
            _make_assistant("claude-opus-4-7", inp=50, out=200, cc=0, cr=0,
                            timestamp=_iso(-1 * 3600)),     # 1 hour ago — in window
        ],
    )

    cutoff = time.time() - 2 * 86400
    ft, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 1
    assert sessions[0]["out"] == 200    # only in-window output counted
    assert ft["opus"]["out"] == 200


def test_window_session_inclusion(fake_projects):
    """Session with all records outside the window is excluded from sessions list."""
    session_a, session_b = fake_projects
    _write_jsonl(
        session_a / "all_old.jsonl",
        [_make_assistant("claude-opus-4-7", inp=10, out=500, cc=0, cr=0,
                         timestamp=_iso(-10 * 86400))],
    )
    _write_jsonl(
        session_b / "has_new.jsonl",
        [_make_assistant("claude-sonnet-4-6", inp=10, out=300, cc=0, cr=0,
                         timestamp=_iso(-1 * 3600))],
    )

    cutoff = time.time() - 2 * 86400
    _, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 1
    assert sessions[0]["fam"] == "sonnet"


def test_malformed_timestamp_record_skipped(fake_projects):
    """Record with unparseable timestamp is skipped; well-formed neighbors still count."""
    session_a, _ = fake_projects
    bad_rec = _make_assistant("claude-opus-4-7", inp=50, out=999, cc=0, cr=0)
    bad_rec["timestamp"] = "not-an-iso-string"
    good_rec = _make_assistant("claude-opus-4-7", inp=20, out=150, cc=0, cr=0,
                               timestamp=_iso(-1 * 3600))
    _write_jsonl(session_a / "mixed_ts.jsonl", [bad_rec, good_rec])

    cutoff = time.time() - 2 * 86400
    ft, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 1
    assert sessions[0]["out"] == 150    # bad record's 999 tokens excluded
    assert ft["opus"]["out"] == 150


def test_walk_credits_subagent_dispatched_cache_tokens(fake_projects):
    """A session's own file plus its <session>/subagents/*.jsonl split files
    both contribute to the reported cache totals -- a raw single-file read
    would undercount cache efficiency for any session that dispatched work
    to a subagent."""
    session_a, _ = fake_projects
    _write_jsonl(
        session_a / "with-subagent.jsonl",
        [_make_assistant("claude-opus-4-7", inp=10, out=50, cc=0, cr=100)],
    )
    _write_subagent_jsonl(
        session_a, "with-subagent", "agent1",
        [_make_assistant("claude-opus-4-7", inp=5, out=20, cc=0, cr=500, sidechain=True)],
    )

    ft, sessions = _mod._walk()

    assert ft["opus"]["cr"] == 600  # 100 (main) + 500 (subagent split file)
    assert sessions[0]["sidechain"] is True


def test_walk_unions_sessions_across_roots(tmp_path):
    """_walk(roots=...) sums sessions found under every root, not just the
    first -- the multi-root union token-analyzer's --top and per-model
    totals depend on."""
    root_a = tmp_path / "acct-a" / "projects"
    proj_a = root_a / "-home-user-repo-a"
    proj_a.mkdir(parents=True)
    _write_jsonl(proj_a / "sess-a.jsonl", [_make_assistant("claude-opus-4-7", inp=10, out=100, cc=0, cr=0)])

    root_b = tmp_path / "acct-b" / "projects"
    proj_b = root_b / "-home-user-repo-b"
    proj_b.mkdir(parents=True)
    _write_jsonl(proj_b / "sess-b.jsonl", [_make_assistant("claude-sonnet-4-6", inp=10, out=200, cc=0, cr=0)])

    ft, sessions = _mod._walk(roots=[root_a, root_b])

    assert len(sessions) == 2
    assert ft["opus"]["out"] == 100
    assert ft["sonnet"]["out"] == 200


def test_main_scans_every_declared_root(monkeypatch, tmp_path):
    """main() must thread _resolve_scan_roots' output into _walk -- verified
    by recording the roots _walk is actually called with, not by re-deriving
    _resolve_scan_roots' return value and calling _walk directly (which would
    still pass even if main() stopped wiring the two together)."""
    active = tmp_path / "active" / "projects"
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active)

    declared_config_dir = tmp_path / "declared-account"
    (declared_config_dir / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_config_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

    captured = {}

    def _recording_walk(since=None, roots=None):
        captured["roots"] = roots
        return {}, []

    monkeypatch.setattr(_mod, "_walk", _recording_walk)
    monkeypatch.setattr(sys, "argv", ["token-analyzer.py"])
    _mod.main()

    assert captured["roots"] == [active, declared_config_dir / "projects"]


def test_main_discloses_resolved_scope_at_one_root(monkeypatch, tmp_path, capsys):
    """main() prints the same unconditional resolved-scope disclosure line
    transcript-analysis.py's funnel subcommands print (_resolved_scope_header),
    even at one root with nothing declared -- without it, a single-account
    scan and a multi-account scan read identically, silently pooling every
    declared account's tokens with no scope signal."""
    active = tmp_path / "active" / "projects"
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active)

    monkeypatch.setattr(sys, "argv", ["token-analyzer.py"])
    _mod.main()

    out = capsys.readouterr().out
    assert "TOKEN ANALYZER SOURCES (*; 1 root (no ~/.claude/transcript-config-dirs declared))" in out


def test_main_discloses_resolved_scope_at_two_roots(monkeypatch, tmp_path, capsys):
    """Same header states the root count unconditionally once a second
    account is declared via ~/.claude/transcript-config-dirs, so a
    multi-account scan is never scope-ambiguous with a single-account one."""
    active = tmp_path / "active" / "projects"
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active)

    declared_config_dir = tmp_path / "declared-account"
    (declared_config_dir / "projects").mkdir(parents=True)
    roots_file = tmp_path / "roots"
    roots_file.write_text(f"{declared_config_dir}\n")
    monkeypatch.setenv("TRANSCRIPT_CONFIG_DIRS_FILE", str(roots_file))

    monkeypatch.setattr(sys, "argv", ["token-analyzer.py"])
    _mod.main()

    out = capsys.readouterr().out
    assert "TOKEN ANALYZER SOURCES (*; 2 roots)" in out


def test_render_report_orders_model_rows_and_skips_absent_families():
    """Row order follows the fixed ('opus', 'sonnet', 'haiku', 'other')
    iteration in _render_report regardless of ft's insertion order, and
    families absent from ft produce no row."""
    ft = {
        "haiku": {"n": 1, "inp": 10, "out": 20, "cc": 0, "cr": 0},
        "opus": {"n": 2, "inp": 100, "out": 200, "cc": 0, "cr": 0},
    }
    report = _mod._render_report(ft, [], label="")

    model_rows = [
        line.split()[0] for line in report.splitlines()
        if line.split() and line.split()[0] in ("opus", "sonnet", "haiku", "other")
    ]
    assert model_rows == ["opus", "haiku"]


def test_render_report_truncates_to_top_10_by_output():
    """Only the 10 highest-output sessions survive (11th's id confirmed
    absent); fam="sonnet" keeps all 11 out of the opus-only candidates
    section so truncation is tested in isolation."""
    sessions = [_make_session(f"sess{i:02d}", fam="sonnet", out=1000 - i * 10) for i in range(11)]

    report = _mod._render_report({}, sessions, label="")

    present = [s["id"] for s in sessions if s["id"] in report]
    assert len(present) == 10
    assert "sess10" not in report  # out=900, the lowest of the 11 -- excluded


def test_render_report_exclusion_breakdown_lists_only_triggered_dimensions():
    """Breakdown line names only the triggered dimension (edits) and omits
    the other five."""
    sessions = [
        _make_session("sess-edit-a", fam="opus", out=600, edits=True),
        _make_session("sess-edit-b", fam="opus", out=700, edits=True),
    ]

    report = _mod._render_report({}, sessions, label="")

    breakdown_line = next(line for line in report.splitlines() if line.startswith("excluded"))
    assert "edits=2" in breakdown_line
    for other_label in ("plan=", "task=", "thinking=", "judgment-skill=", "sidechain="):
        assert other_label not in breakdown_line


def test_render_report_shows_none_found_when_no_candidates():
    """Zero qualifying candidates renders the literal 'None found.' line."""
    sessions = [_make_session("sess-a", fam="opus", out=600, plan=True)]
    report = _mod._render_report({}, sessions, label="")
    assert "None found." in report


def test_render_report_full_report_whitespace_fidelity():
    """Exercises every section at once (2 model families, 11 sessions,
    non-empty exclusion breakdown and candidates) against the exact
    returned string, not a substring -- a bad blank-line split would
    silently drop or duplicate a line that substring checks wouldn't
    catch."""
    ft = {
        "opus": {"n": 3, "inp": 1000, "out": 5000, "cc": 100, "cr": 200},
        "sonnet": {"n": 2, "inp": 500, "out": 800, "cc": 0, "cr": 50},
    }
    session_specs = [
        ("s0", "opus", 1100, {}),
        ("s1", "sonnet", 1000, {}),
        ("s2", "opus", 900, {"edits": True}),
        ("s3", "opus", 800, {"plan": True}),
        ("s4", "sonnet", 700, {}),
        ("s5", "opus", 600, {}),
        ("s6", "opus", 500, {"task": True}),
        ("s7", "opus", 400, {}),
        ("s8", "sonnet", 300, {}),
        ("s9", "opus", 200, {}),
        ("s10", "opus", 100, {}),
    ]
    sessions = [
        _make_session(sid, proj=f"proj-{sid}", fam=fam, out=out, inp=out * 2, **extra)
        for sid, fam, out, extra in session_specs
    ]

    report = _mod._render_report(ft, sessions, label="")

    assert report == (
        "## Per-model token summary\n"
        "\n"
        "Model    Sessions        Input       Output  CacheCreate    CacheRead  HitRate\n"
        "------------------------------------------------------------------------------\n"
        "opus            3        1,000        5,000          100          200      17%\n"
        "sonnet          2          500          800            0           50       9%\n"
        "\n"
        "## Top 10 sessions by output tokens\n"
        "\n"
        "     Session  Model         Output       Input  Plan  Edits  Project\n"
        "------------------------------------------------------------------------------\n"
        "          s0  opus           1,100       2,200     N      N  proj-s0\n"
        "          s1  sonnet         1,000       2,000     N      N  proj-s1\n"
        "          s2  opus             900       1,800     N      Y  proj-s2\n"
        "          s3  opus             800       1,600     Y      N  proj-s3\n"
        "          s4  sonnet           700       1,400     N      N  proj-s4\n"
        "          s5  opus             600       1,200     N      N  proj-s5\n"
        "          s6  opus             500       1,000     N      N  proj-s6\n"
        "          s7  opus             400         800     N      N  proj-s7\n"
        "          s8  sonnet           300         600     N      N  proj-s8\n"
        "          s9  opus             200         400     N      N  proj-s9\n"
        "\n"
        "## Opus → Sonnet candidates (2 sessions: no plan-mode, no edits, no task/thinking/judgment-skill/sidechain)\n"
        "\n"
        "excluded 3 high-output Opus sessions: plan=1 edits=1 task=1\n"
        "     Session      Output       Input  Project\n"
        "--------------------------------------------------\n"
        "          s0       1,100       2,200  proj-s0\n"
        "          s5         600       1,200  proj-s5\n"
        "\n"
        "(For per-turn analysis use: transcript-analysis.py audit-routing)\n"
        "(For price-weighted dollar cost by token class/model/context bucket use: transcript-analysis.py cost)"
    )


def test_main_wires_since_argument_into_walk(monkeypatch, tmp_path, capsys):
    """Asserts _walk receives the --since epoch, bracketed by time.time()
    calls before/after main() to avoid a flaky exact-timestamp comparison."""
    active = tmp_path / "active" / "projects"
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active)

    captured = {}

    def _recording_walk(since=None, roots=None):
        captured["since"] = since
        return {}, []

    monkeypatch.setattr(_mod, "_walk", _recording_walk)
    monkeypatch.setattr(sys, "argv", ["token-analyzer.py", "--since", "2d"])

    before = time.time() - 2 * 86400
    _mod.main()
    after = time.time() - 2 * 86400

    assert before <= captured["since"] <= after
    # _walk is stubbed but _render_report still runs for real inside main() --
    # confirm the --since label actually reaches the printed report.
    assert "(activity in the last 2d)" in capsys.readouterr().out


def test_main_scans_active_root_alone_when_no_roots_declared(monkeypatch, tmp_path):
    """With no ~/.claude/transcript-config-dirs declared, main() must call
    _walk with roots == [active] -- the single-root counterpart to
    test_main_scans_every_declared_root, which only covers the two-root
    case."""
    active = tmp_path / "active" / "projects"
    monkeypatch.setattr(_mod.scope, "PROJECTS_DIR", active)

    captured = {}

    def _recording_walk(since=None, roots=None):
        captured["roots"] = roots
        return {}, []

    monkeypatch.setattr(_mod, "_walk", _recording_walk)
    monkeypatch.setattr(sys, "argv", ["token-analyzer.py"])
    _mod.main()

    assert captured["roots"] == [active]
