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
import importlib.util
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from _config_dir import config_dir

# Fallback/display-only: main()'s actual scan roots come from
# _transcript_analysis._resolve_scan_roots/_session_meta_dir_for_root, which
# read that module's own PROJECTS_DIR, not this one -- monkeypatching this
# file's PROJECTS_DIR no longer affects what main() scans.
CLAUDE_DIR = config_dir()
PROJECTS_DIR = CLAUDE_DIR / "projects"
SESSION_META_DIR = CLAUDE_DIR / "usage-data" / "session-meta"

# transcript-analysis.py is a hyphenated filename, not a valid `import`
# identifier -- load it by path, mirroring how this suite's own tests load
# hyphenated sibling scripts (e.g. tests/test_analyze_context.py).
_TRANSCRIPT_ANALYSIS_PATH = Path(__file__).parent / "transcript-analysis.py"
sys.path.insert(0, str(_TRANSCRIPT_ANALYSIS_PATH.parent))
_transcript_analysis_spec = importlib.util.spec_from_file_location(
    "transcript_analysis", _TRANSCRIPT_ANALYSIS_PATH
)
_transcript_analysis = importlib.util.module_from_spec(_transcript_analysis_spec)
_transcript_analysis_spec.loader.exec_module(_transcript_analysis)


def cwd_to_project_key(cwd: Path) -> str:
    return str(cwd).replace("/", "-")


def _find_session_in_root(session_id: str, root: Path) -> Path | None:
    """Search one root's transcripts for session_id, exact then prefix match.

    A plain filename glob, not iter_sessions: this only needs each main
    session file's own stem, and iter_sessions' subagent-merge (subagent
    files live one level deeper, under <session>/subagents/, and are never
    exposed as their own path here) would read every transcript's full
    content for no benefit to a filename match.
    """
    if not root.exists():
        return None
    prefix_matches: list[Path] = []
    for jsonl in sorted(root.glob("*/*.jsonl")):
        if jsonl.stem == session_id:
            return jsonl
        if jsonl.stem.startswith(session_id):
            prefix_matches.append(jsonl)
    return prefix_matches[0] if prefix_matches else None


def find_session_jsonl(session_id: str, roots: Sequence[Path]) -> Path | None:
    """Return session_id's transcript, searching roots in order (active
    profile first). A short id can match a different session under another
    declared root; that ambiguity is warned on stderr rather than resolved
    silently to whichever root happened to be active.
    """
    found = [m for root in roots if (m := _find_session_in_root(session_id, root)) is not None]
    if not found:
        return None
    winner = found[0]
    if len({m.resolve() for m in found}) > 1:
        print(
            f"find_session_jsonl: session id {session_id!r} matches different sessions "
            f"across declared roots; using the active profile's match ({winner})",
            file=sys.stderr,
        )
    return winner


def latest_session_jsonl(project_key: str, roots: Sequence[Path]) -> tuple[str, Path] | None:
    """Return the most recently modified session for project_key, comparing
    mtimes across every root -- the same cwd's project dir can exist under
    more than one declared root, and "latest" means latest overall, not
    latest in whichever root happens to be active.
    """
    candidates: list[Path] = []
    for root in roots:
        project_dir = root / project_key
        if project_dir.exists():
            candidates.extend(project_dir.glob("*.jsonl"))
    if not candidates:
        return None
    latest = max(candidates, key=lambda p: p.stat().st_mtime)
    return latest.stem, latest


def parse_turns(jsonl_path: Path) -> list[dict]:
    """Extract per-turn usage fields, merging subagent transcripts.

    Reads via _read_session_file(include_subagents=True) so a session that
    dispatched work to a subagent contributes that subagent's turns to the
    growth curve too, instead of silently under-reporting it. Sorted by
    timestamp afterward: _read_session_file appends subagent-file records
    after all main-file records, and the growth curve's turn-by-turn deltas
    are only meaningful in chronological order.
    """
    records = _transcript_analysis._read_session_file(jsonl_path, include_subagents=True)
    turns = []
    for record in records:
        try:
            msg = record.get("message", {})
            if not isinstance(msg, dict) or "usage" not in msg:
                continue
            u = msg["usage"]
            total_in = (
                u.get("input_tokens", 0)
                + u.get("cache_creation_input_tokens", 0)
                + u.get("cache_read_input_tokens", 0)
            )
            raw_ts = record.get("timestamp") or ""
            turns.append({
                "total_in": total_in,
                "output": u.get("output_tokens", 0),
                "is_sidechain": record.get("isSidechain", False),
                "skill": record.get("attributionSkill") or "",
                "ts": raw_ts[:16],
                "_sort_ts": raw_ts,
                "record_type": record.get("type", ""),
            })
        except (KeyError, TypeError):
            continue
    turns.sort(key=lambda t: t["_sort_ts"])
    for t in turns:
        del t["_sort_ts"]
    return turns


def render_bar(value: int, peak: int, width: int = 40) -> str:
    filled = int(value / peak * width) if peak > 0 else 0
    return "█" * filled


def analyze_session(session_id: str, jsonl_path: Path, roots: Sequence[Path]) -> None:
    _transcript_analysis._print_resolved_scope("session", session_id, roots)
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


def _session_meta_dir_for_root(root: Path) -> Path:
    """Return the usage-data/session-meta dir paired with a projects/ root.

    Session metadata lives per config dir, alongside that config dir's own
    projects/ -- each root's sessions must be paired with that same root's
    own metadata, not always the active profile's SESSION_META_DIR.
    """
    return root.parent / "usage-data" / "session-meta"


def show_top(count: int, roots: Sequence[Path]) -> None:
    _transcript_analysis._print_resolved_scope("top", "*", roots)
    meta_dirs = [_session_meta_dir_for_root(root) for root in roots]
    if not any(d.exists() for d in meta_dirs):
        print(
            f"Session metadata directory not found in any of {len(roots)} scanned roots",
            file=sys.stderr,
        )
        sys.exit(1)

    sessions = []
    for meta_dir in meta_dirs:
        for meta_file in meta_dir.glob("*.json"):
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
    roots = _transcript_analysis._resolve_scan_roots(args)

    if args.top is not None:
        show_top(args.top, roots)
        return

    if args.session_id:
        jsonl = find_session_jsonl(args.session_id, roots)
        if jsonl is None:
            print(f"Session not found: {args.session_id}", file=sys.stderr)
            sys.exit(1)
        analyze_session(args.session_id, jsonl, roots)
        return

    project_key = cwd_to_project_key(Path.cwd())
    result = latest_session_jsonl(project_key, roots)
    if result is None:
        print(
            f"No sessions found for project directory: {Path.cwd()}\n"
            "Run from the project root, or pass a session ID explicitly.",
            file=sys.stderr,
        )
        sys.exit(1)
    session_id, jsonl_path = result
    analyze_session(session_id, jsonl_path, roots)


if __name__ == "__main__":
    main()
