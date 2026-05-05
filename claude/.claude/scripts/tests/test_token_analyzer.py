"""Smoke tests for token-analyzer.py."""
import importlib.util
import json
import os
import time
from pathlib import Path

import pytest

# Load token-analyzer as a module without it being on sys.path as a package.
_SCRIPT = Path(__file__).parent.parent / "token-analyzer.py"
_spec = importlib.util.spec_from_file_location("token_analyzer", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
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
    return rec


def _make_user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


def _iso(offset_seconds: float = 0) -> str:
    """Return an ISO 8601 UTC timestamp offset from now by offset_seconds (negative = past)."""
    from datetime import UTC, datetime
    return datetime.fromtimestamp(time.time() + offset_seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture()
def fake_projects(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    proj_a = projects / "-home-user-repo"
    proj_b = projects / "-home-user-other"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)
    monkeypatch.setattr(_mod, "PROJECTS_DIR", projects)
    return proj_a, proj_b


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
