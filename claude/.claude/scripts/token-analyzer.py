#!/usr/bin/env python3
"""token-analyzer.py — per-model token breakdown across Claude Code sessions. No network; no writes."""
import argparse
import json
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
JUDGMENT_SKILLS = frozenset({
    "code-review", "plan-review", "security-review",
    "skill-review", "respond-pr", "ultrareview",
})
def _fam(m: str) -> str:
    if "opus" in m:
        return "opus"
    if "sonnet" in m:
        return "sonnet"
    if "haiku" in m:
        return "haiku"
    return "other"


def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "—"


def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _ts_in_window(ts_str: str | None, since: float) -> bool:
    """Return True if ts_str parses to a timestamp >= since. Missing or malformed strings return False."""
    if not ts_str:
        return False
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp() >= since
    except (ValueError, TypeError):
        return False


def _walk(since: float | None = None):
    family_totals = defaultdict(lambda: {"n": 0, "inp": 0, "out": 0, "cc": 0, "cr": 0})
    sessions = []
    for jsonl in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        if since is not None and jsonl.stat().st_mtime < since:
            continue
        proj = jsonl.parent.name.lstrip("-").replace("-", "/", 2).split("/", 2)[-1]
        fam_out_main = defaultdict(int)  # non-sidechain output per family, for dominant-fam
        fam_window: dict[str, dict] = defaultdict(lambda: {"inp": 0, "out": 0, "cc": 0, "cr": 0})
        fams_in_session: set[str] = set()
        plan, edits, task, thinking, judgment_skill, sidechain = False, False, False, False, False, False
        has_window_record = False
        try:
            with open(jsonl) as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    rtype = rec.get("type", "")
                    is_side = bool(rec.get("isSidechain"))
                    if is_side:
                        sidechain = True
                    msg = rec.get("message", {})
                    if rtype in ("user", "human") and "Plan mode is active" in _content_text(msg.get("content", "")):
                        plan = True
                    if rtype == "assistant" and isinstance(msg, dict) and "usage" in msg:
                        u = msg["usage"]
                        fam = _fam(msg.get("model", "").lower())
                        in_window = _ts_in_window(rec.get("timestamp"), since) if since is not None else True
                        if in_window:
                            inp, out, cc, cr = (u.get(k, 0) for k in (
                                "input_tokens", "output_tokens",
                                "cache_creation_input_tokens", "cache_read_input_tokens"))
                            fam_window[fam]["inp"] += inp
                            fam_window[fam]["out"] += out
                            fam_window[fam]["cc"] += cc
                            fam_window[fam]["cr"] += cr
                            fams_in_session.add(fam)
                            has_window_record = True
                            if not is_side:
                                fam_out_main[fam] += out
                        if not is_side:
                            content = msg.get("content", []) or []
                            if not edits and any(isinstance(b, dict) and b.get("name") in EDIT_TOOLS for b in content):
                                edits = True
                            if not task and any(isinstance(b, dict) and b.get("name") == "Task" for b in content):
                                task = True
                            if not thinking and any(isinstance(b, dict) and b.get("type") == "thinking" for b in content):
                                thinking = True
                            if not judgment_skill and any(
                                isinstance(b, dict) and b.get("name") == "Skill"
                                and b.get("input", {}).get("skill") in JUDGMENT_SKILLS
                                for b in content
                            ):
                                judgment_skill = True
        except OSError:
            continue
        if since is not None and not has_window_record:
            continue
        total_out = sum(v["out"] for v in fam_window.values())
        if not total_out:
            continue
        for fam in fams_in_session:
            t = family_totals[fam]
            t["n"] += 1
            for k in ("inp", "out", "cc", "cr"):
                t[k] += fam_window[fam][k]
        dom = max(fam_out_main, key=fam_out_main.get) if fam_out_main else "other"
        sessions.append({
            "id": jsonl.stem[:12], "proj": proj, "fam": dom,
            "out": total_out, "inp": sum(v["inp"] for v in fam_window.values()),
            "plan": plan, "edits": edits, "task": task,
            "thinking": thinking, "judgment_skill": judgment_skill, "sidechain": sidechain,
        })
    return family_totals, sessions


def main():
    parser = argparse.ArgumentParser(description="Per-model token breakdown across Claude Code sessions.")
    parser.add_argument("--since", metavar="Nd", help="Limit to token activity within the last N days (e.g. 2d, 7d)")
    args = parser.parse_args()

    since = None
    if args.since:
        try:
            days = float(args.since.rstrip("d"))
            since = time.time() - days * 86400
        except ValueError:
            parser.error(f"--since: expected a number of days like '2d' or '7', got {args.since!r}")

    label = f" (activity in the last {args.since})" if args.since else ""
    ft, sessions = _walk(since)

    print(f"## Per-model token summary{label}\n")
    print(f"{'Model':<8} {'Sessions':>8} {'Input':>12} {'Output':>12} {'CacheCreate':>12} {'CacheRead':>12} {'HitRate':>8}")
    print("-" * 78)
    for fam in ("opus", "sonnet", "haiku", "other"):
        t = ft.get(fam)
        if not t:
            continue
        print(f"{fam:<8} {t['n']:>8} {t['inp']:>12,} {t['out']:>12,} {t['cc']:>12,} {t['cr']:>12,} "
              f"{_pct(t['cr'], t['cr'] + t['inp']):>8}")

    top = sorted(sessions, key=lambda s: s["out"], reverse=True)[:10]
    print(f"\n## Top 10 sessions by output tokens{label}\n")
    print(f"{'Session':>12}  {'Model':<8}  {'Output':>10}  {'Input':>10}  Plan  Edits  Project")
    print("-" * 78)
    for s in top:
        print(f"{s['id']:>12}  {s['fam']:<8}  {s['out']:>10,}  {s['inp']:>10,}  "
              f"{'Y' if s['plan'] else 'N':>4}  {'Y' if s['edits'] else 'N':>5}  {s['proj']}")

    _excl_keys = ("plan", "edits", "task", "thinking", "judgment_skill", "sidechain")
    _excl_labels = ("plan", "edits", "task", "thinking", "judgment-skill", "sidechain")
    opus_high = [s for s in sessions if s["fam"] == "opus" and s["out"] >= 500]
    non_cands = [s for s in opus_high if any(s[k] for k in _excl_keys)]
    cands = sorted(
        [s for s in opus_high if not any(s[k] for k in _excl_keys)],
        key=lambda s: s["out"], reverse=True,
    )
    excl_desc = "no plan-mode, no edits, no task/thinking/judgment-skill/sidechain"
    print(f"\n## Opus → Sonnet candidates ({len(cands)} sessions: {excl_desc})\n")
    if non_cands:
        excl_parts = " ".join(
            f"{lbl}={sum(1 for s in non_cands if s[key])}"
            for key, lbl in zip(_excl_keys, _excl_labels, strict=False)
            if any(s[key] for s in non_cands)
        )
        print(f"excluded {len(non_cands)} high-output Opus sessions: {excl_parts}")
    if cands:
        print(f"{'Session':>12}  {'Output':>10}  {'Input':>10}  Project")
        print("-" * 50)
        for s in cands:
            print(f"{s['id']:>12}  {s['out']:>10,}  {s['inp']:>10,}  {s['proj']}")
    else:
        print("None found.")
    print("\n(For per-turn analysis use: transcript-analysis.py audit-routing)")


if __name__ == "__main__":
    main()
