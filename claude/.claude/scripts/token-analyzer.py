#!/usr/bin/env python3
"""token-analyzer.py — per-model token breakdown across Claude Code sessions. No network; no writes."""
import json
from collections import defaultdict
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
_fam = lambda m: "opus" if "opus" in m else "sonnet" if "sonnet" in m else "haiku" if "haiku" in m else "other"
_pct = lambda n, d: f"{100 * n / d:.0f}%" if d else "—"

def _content_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _walk():
    family_totals = defaultdict(lambda: {"n": 0, "inp": 0, "out": 0, "cc": 0, "cr": 0})
    sessions = []
    for jsonl in sorted(PROJECTS_DIR.glob("*/*.jsonl")):
        proj = jsonl.parent.name.lstrip("-").replace("-", "/", 2).split("/", 2)[-1]
        fam_out, tot = defaultdict(int), {"inp": 0, "out": 0, "cc": 0, "cr": 0}
        plan, edits = False, False
        try:
            with open(jsonl) as fh:
                for raw in fh:
                    try:
                        rec = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    rtype, msg = rec.get("type", ""), rec.get("message", {})
                    if rtype in ("user", "human") and "Plan mode is active" in _content_text(msg.get("content", "")):
                        plan = True
                    if rtype == "assistant" and isinstance(msg, dict) and "usage" in msg:
                        u = msg["usage"]
                        fam = _fam(msg.get("model", "").lower())
                        inp, out, cc, cr = (u.get(k, 0) for k in (
                            "input_tokens", "output_tokens",
                            "cache_creation_input_tokens", "cache_read_input_tokens"))
                        fam_out[fam] += out
                        tot["inp"] += inp; tot["out"] += out; tot["cc"] += cc; tot["cr"] += cr
                        if not edits and any(
                            isinstance(b, dict) and b.get("name") in EDIT_TOOLS
                            for b in msg.get("content", [])
                        ):
                            edits = True
        except OSError:
            continue
        if not tot["out"]:
            continue
        dom = max(fam_out, key=fam_out.get) if fam_out else "other"
        t = family_totals[dom]
        t["n"] += 1
        for k in ("inp", "out", "cc", "cr"):
            t[k] += tot[k]
        sessions.append({"id": jsonl.stem[:12], "proj": proj, "fam": dom,
                         "out": tot["out"], "inp": tot["inp"], "plan": plan, "edits": edits})
    return family_totals, sessions


def main():
    ft, sessions = _walk()

    print("## Per-model token summary\n")
    print(f"{'Model':<8} {'Sessions':>8} {'Input':>12} {'Output':>12} {'CacheCreate':>12} {'CacheRead':>12} {'HitRate':>8}")
    print("-" * 78)
    for fam in ("opus", "sonnet", "haiku", "other"):
        t = ft.get(fam)
        if not t:
            continue
        print(f"{fam:<8} {t['n']:>8} {t['inp']:>12,} {t['out']:>12,} {t['cc']:>12,} {t['cr']:>12,} "
              f"{_pct(t['cr'], t['cr'] + t['inp']):>8}")

    top = sorted(sessions, key=lambda s: s["out"], reverse=True)[:10]
    print(f"\n## Top 10 sessions by output tokens\n")
    print(f"{'Session':>12}  {'Model':<8}  {'Output':>10}  {'Input':>10}  Plan  Edits  Project")
    print("-" * 78)
    for s in top:
        print(f"{s['id']:>12}  {s['fam']:<8}  {s['out']:>10,}  {s['inp']:>10,}  "
              f"{'Y' if s['plan'] else 'N':>4}  {'Y' if s['edits'] else 'N':>5}  {s['proj']}")

    cands = sorted(
        (s for s in sessions if s["fam"] == "opus" and not s["plan"] and not s["edits"] and s["out"] >= 500),
        key=lambda s: s["out"], reverse=True,
    )
    print(f"\n## Opus → Sonnet candidates ({len(cands)} sessions: no plan-mode, no edits)\n")
    if cands:
        print(f"{'Session':>12}  {'Output':>10}  {'Input':>10}  Project")
        print("-" * 50)
        for s in cands:
            print(f"{s['id']:>12}  {s['out']:>10,}  {s['inp']:>10,}  {s['proj']}")
    else:
        print("None found.")


if __name__ == "__main__":
    main()
