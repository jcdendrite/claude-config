#!/usr/bin/env python3
"""
analyze-context.py — inspect Claude Code session context growth.

Reads session data from ~/.claude/ (both JSONL conversation logs and
usage-data session metadata). No network calls; no writes.

Usage:
    analyze-context.py                    # latest session in current project
    analyze-context.py <session-id>       # specific session by ID
    analyze-context.py --top [N]          # N heaviest sessions across all projects (default 10)

The per-session view shows context window size (input + cache tokens) per
turn, a growth curve, and the turns where the biggest jumps occurred.

The --top view ranks by direct input + output tokens from session metadata
(cache-read tokens excluded). Useful for identifying which sessions to
investigate with the per-session view.
"""

import argparse
import json
import sys
from pathlib import Path

CLAUDE_DIR = Path.home() / ".claude"
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSION_META_DIR = CLAUDE_DIR / "usage-data" / "session-meta"


def cwd_to_project_key(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


def find_session_jsonl(session_id: str) -> Path | None:
    if not PROJECTS_DIR.exists():
        return None
    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            return candidate
        # Support prefix matching (e.g. the 12-char prefix shown in --top output).
        matches = sorted(project_dir.glob(f"{session_id}*.jsonl"))
        if matches:
            return matches[0]
    return None


def latest_session_jsonl(project_key: str) -> tuple[str, Path] | None:
    project_dir = PROJECTS_DIR / project_key
    if not project_dir.exists():
        return None
    jsonls = sorted(
        project_dir.glob("*.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return (jsonls[0].stem, jsonls[0]) if jsonls else None


def parse_turns(jsonl_path: Path) -> list[dict]:
    turns = []
    with open(jsonl_path) as fh:
        for raw in fh:
            try:
                record = json.loads(raw)
                msg = record.get("message", {})
                if not isinstance(msg, dict) or "usage" not in msg:
                    continue
                u = msg["usage"]
                total_in = (
                    u.get("input_tokens", 0)
                    + u.get("cache_creation_input_tokens", 0)
                    + u.get("cache_read_input_tokens", 0)
                )
                turns.append({
                    "total_in": total_in,
                    "output": u.get("output_tokens", 0),
                    "is_sidechain": record.get("isSidechain", False),
                    "skill": record.get("attributionSkill") or "",
                    "ts": (record.get("timestamp") or "")[:16],
                    "record_type": record.get("type", ""),
                })
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return turns


def render_bar(value: int, peak: int, width: int = 40) -> str:
    filled = int(value / peak * width) if peak > 0 else 0
    return "█" * filled


def analyze_session(session_id: str, jsonl_path: Path) -> None:
    turns = parse_turns(jsonl_path)
    if not turns:
        print(f"No usage data found in {jsonl_path}", file=sys.stderr)
        sys.exit(1)

    start_tokens = turns[0]["total_in"]
    peak_tokens = max(t["total_in"] for t in turns)
    end_tokens = turns[-1]["total_in"]
    growth = end_tokens - start_tokens
    avg_per_turn = growth / max(len(turns) - 1, 1)

    print(f"Session : {session_id}")
    print(f"File    : {jsonl_path}")
    print(f"Turns   : {len(turns)}")
    print(f"Start   : {start_tokens:>10,}  tokens (context window at turn 0)")
    print(f"Peak    : {peak_tokens:>10,}  tokens")
    print(f"End     : {end_tokens:>10,}  tokens")
    print(f"Growth  : {growth:>+10,}  tokens  ({avg_per_turn:,.0f}/turn avg)")
    print()

    print("Growth curve (context window size per turn):")
    step = max(1, len(turns) // 12)
    shown_indices = set(range(0, len(turns), step)) | {len(turns) - 1}
    for i in sorted(shown_indices):
        t = turns[i]
        bar = render_bar(t["total_in"], peak_tokens)
        ts = t["ts"][11:] if t["ts"] else "     "
        print(f"  turn {i:4}  {ts}  {t['total_in']:>10,}  {bar}")
    print()

    jumps = [
        (turns[i]["total_in"] - turns[i - 1]["total_in"], i, turns[i])
        for i in range(1, len(turns))
        if turns[i]["total_in"] > turns[i - 1]["total_in"]
    ]
    jumps.sort(reverse=True)

    print("Biggest single-turn context jumps:")
    for delta, idx, t in jumps[:10]:
        label = t["skill"] or t["record_type"] or ""
        sidechain_tag = "  [sidechain]" if t["is_sidechain"] else ""
        ts = t["ts"][11:] if t["ts"] else "     "
        print(f"  turn {idx:4}  {ts}  +{delta:>9,}  {label}{sidechain_tag}")


def show_top(count: int) -> None:
    if not SESSION_META_DIR.exists():
        print(f"Session metadata directory not found: {SESSION_META_DIR}", file=sys.stderr)
        sys.exit(1)

    sessions = []
    for meta_file in SESSION_META_DIR.glob("*.json"):
        try:
            with open(meta_file) as fh:
                d = json.load(fh)
            direct_tokens = d.get("input_tokens", 0) + d.get("output_tokens", 0)
            sessions.append({
                "direct_tokens": direct_tokens,
                "session_id": d.get("session_id", ""),
                "project_path": d.get("project_path", "") or "",
                "start_date": (d.get("start_time") or "")[:10],
                "duration_min": d.get("duration_minutes", 0) or 0,
                "agent_calls": (d.get("tool_counts") or {}).get("Agent", 0),
            })
        except (json.JSONDecodeError, KeyError, OSError, TypeError):
            continue

    sessions.sort(key=lambda s: s["direct_tokens"], reverse=True)

    print(f"Top {count} sessions by direct token usage (input + output; cache-read excluded)\n")
    print(f"{'Tokens':>12}  {'Duration':>9}  {'Agents':>6}  {'Date':>10}  Project  (session-id)")
    print("-" * 78)
    for s in sessions[:count]:
        dur = f"{s['duration_min']:.0f}m"
        project = Path(s["project_path"]).name if s["project_path"] else "?"
        sid = s["session_id"][:12]
        print(
            f"{s['direct_tokens']:>12,}  {dur:>9}  {s['agent_calls']:>6}  "
            f"{s['start_date']:>10}  {project}  ({sid})"
        )
    print()
    print("Re-run with a session ID to see its per-turn context growth:")
    print("  analyze-context.py <session-id>")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect Claude Code session context growth.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  analyze-context.py                    latest session in current project
  analyze-context.py --top              10 heaviest sessions across all projects
  analyze-context.py --top 20           20 heaviest sessions
  analyze-context.py <session-id>       specific session by full or 12-char prefix
""",
    )
    parser.add_argument("session_id", nargs="?", help="session ID to analyze")
    parser.add_argument(
        "--top",
        nargs="?",
        const=10,
        type=int,
        metavar="N",
        help="show N heaviest sessions by direct token usage (default 10)",
    )
    args = parser.parse_args()

    if args.top is not None:
        show_top(args.top)
        return

    if args.session_id:
        jsonl = find_session_jsonl(args.session_id)
        if jsonl is None:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            sys.exit(1)
        analyze_session(args.session_id, jsonl)
        return

    project_key = cwd_to_project_key(Path.cwd())
    result = latest_session_jsonl(project_key)
    if result is None:
        print(
            f"No sessions found for project directory: {Path.cwd()}\n"
            "Run from the project root, or pass a session ID explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    session_id, jsonl_path = result
    analyze_session(session_id, jsonl_path)


if __name__ == "__main__":
    main()
