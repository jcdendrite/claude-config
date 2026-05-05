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


def _make_assistant(model: str, inp: int, out: int, cc: int, cr: int, tool_names: list[str] | None = None) -> dict:
    content = [{"type": "tool_use", "name": n} for n in (tool_names or [])]
    return {
        "type": "assistant",
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


def _make_user(text: str) -> dict:
    return {"type": "user", "message": {"content": text}}


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
    cands = [s for s in sessions if s["fam"] == "opus" and not s["plan"] and not s["edits"] and s["out"] >= 500]
    non_cands = [s for s in sessions if s["plan"]]

    assert len(cands) == 1, f"expected 1 candidate, got {cands}"
    assert len(non_cands) == 1
    assert cands[0]["out"] == 800


def test_edit_tool_excludes_candidate(fake_projects):
    session_a, _ = fake_projects
    # Opus session with an Edit call → not a candidate
    _write_jsonl(
        session_a / "with_edit.jsonl",
        [_make_assistant("claude-opus-4-7", inp=50, out=600, cc=0, cr=0, tool_names=["Edit"])],
    )

    _, sessions = _mod._walk()
    cands = [s for s in sessions if s["fam"] == "opus" and not s["plan"] and not s["edits"] and s["out"] >= 500]
    assert len(cands) == 0


def test_since_filter(fake_projects):
    session_a, session_b = fake_projects
    old_file = session_a / "old_sess.jsonl"
    new_file = session_b / "new_sess.jsonl"

    _write_jsonl(old_file, [_make_assistant("claude-opus-4-7", inp=10, out=100, cc=0, cr=0)])
    _write_jsonl(new_file, [_make_assistant("claude-sonnet-4-6", inp=10, out=200, cc=0, cr=0)])

    # Back-date the old file to 10 days ago
    old_mtime = time.time() - 10 * 86400
    os.utime(old_file, (old_mtime, old_mtime))

    # --since 2d should include new_file only
    cutoff = time.time() - 2 * 86400
    ft, sessions = _mod._walk(since=cutoff)

    assert len(sessions) == 1
    assert sessions[0]["fam"] == "sonnet"
    assert ft.get("opus") is None or ft["opus"]["n"] == 0
