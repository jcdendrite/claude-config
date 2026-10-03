#!/usr/bin/env python3
"""Passive-execution audit for /review-pr Step 2.

Reads a JSON array of PR-changed file paths on stdin (the full,
paginated changed-file list -- see REFERENCES.md on why the raw `files`
field alone is not safe input here) and reports which paths could run code
or load instructions with no explicit run: git filter drivers at checkout,
hook runners under a conventional `core.hooksPath` directory, or files the
reviewing harness may load from a project directory. A pure function of a path list: no `gh` call, no
repo checkout, no LLM judgment -- review-pr-checkout.sh and
review-pr-diff.sh invoke this script rather than leaving the match logic
to prose interpretation.

Content-blind by design: a hit means the path could be an execution-surface
file, not that its content is malicious -- over-flagging is the accepted
direction, since a stop never silently skips a file.

Every match folds case, since a case-insensitive filesystem (macOS
default, Windows) resolves a differently-cased path to the same loaded
file -- `.MCP.json` loads exactly like `.mcp.json` there.

Output (stdout, one JSON object): {"stop": bool, "matches": [{"path":
str, "reason": str}, ...]}. Exit status mirrors "stop": 1 when any path
matched (the checkout script refuses; the diff script reports it), 0 when
none did. A malformed stdin payload (not JSON, not an array of strings) prints
{"error": str} and exits 2 -- distinct from both outcomes above, since
"could not audit" must never read as "audited clean."
"""
from __future__ import annotations

import json
import sys

# Conventional core.hooksPath target directory names -- a heuristic over
# common target-dir names, not a read of the actual configured value (see
# REFERENCES.md).
_HOOKSPATH_TARGET_DIRS = (".githooks", ".husky")


def _lower_posix_path(path: str) -> str:
    """Normalize a path for matching: forward slashes, folded case."""
    return path.replace("\\", "/").casefold()


def _classify(path: str) -> str | None:
    """Return the reason `path` is a passive-execution-surface hit, or None."""
    lower = _lower_posix_path(path)
    segments = [seg for seg in lower.split("/") if seg]
    if not segments:
        return None

    if segments[-1] == ".gitattributes":
        return "git executes .gitattributes clean/smudge filter drivers at checkout"

    if any(seg in _HOOKSPATH_TARGET_DIRS for seg in segments[:-1]):
        return "path sits under a conventional core.hooksPath target directory that a repo's hook runner may execute"

    if segments[-1] == "claude.md":
        return "CLAUDE.md is loaded as standing instructions by the reviewing harness"

    if segments[-1] == "claude.local.md":
        return (
            "CLAUDE.local.md is concatenated into the same standing-instructions load as "
            "CLAUDE.md (claude/.claude/rules/claude-md-conventions.md's precedence list)"
        )

    if segments[-1] == "agents.md":
        return (
            "AGENTS.md may load as standing instructions through a CLAUDE.md @AGENTS.md import, "
            "which resolves relative to the importing file"
        )

    if segments[-1] == "skill.md" and (
        lower.startswith(".claude/skills/") or "/.claude/skills/" in lower
    ):
        return ".claude/skills/**/SKILL.md is loaded as skill instructions by the harness's project-level skill discovery"

    if lower == ".claude/settings.json" or lower.endswith("/.claude/settings.json"):
        return ".claude/settings.json configures hooks and permissions the harness applies"

    if lower == ".claude/settings.local.json" or lower.endswith("/.claude/settings.local.json"):
        return ".claude/settings.local.json configures hooks and permissions the harness applies, same as settings.json"

    if lower.startswith(".claude/hooks/") or "/.claude/hooks/" in lower:
        return ".claude/hooks/** runs on every matching tool call the harness makes"

    if lower.startswith(".claude/agents/") or "/.claude/agents/" in lower:
        return ".claude/agents/** defines subagent behavior the harness may dispatch"

    if lower.startswith(".claude/rules/") or "/.claude/rules/" in lower:
        return ".claude/rules/** may load as standing instructions, unconditionally when it has no paths: key"

    if segments[-1] == ".mcp.json":
        return ".mcp.json registers an MCP server the harness may launch"

    if segments[-1] == ".gitmodules":
        return ".gitmodules can point a submodule fetch/checkout at attacker-controlled content"

    return None


def audit_execution_surface(paths: list[str]) -> dict:
    """Pure path-list -> {"stop": bool, "matches": [...]} predicate."""
    matches = []
    for path in paths:
        reason = _classify(path)
        if reason is not None:
            matches.append({"path": path, "reason": reason})
    return {"stop": bool(matches), "matches": matches}


def main() -> int:
    raw = sys.stdin.read()
    try:
        paths = json.loads(raw) if raw.strip() else []
    except json.JSONDecodeError as exc:
        print(json.dumps({"error": f"invalid JSON on stdin: {exc}"}))
        return 2
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        print(json.dumps({"error": "stdin must be a JSON array of path strings"}))
        return 2

    result = audit_execution_surface(paths)
    print(json.dumps(result))
    return 1 if result["stop"] else 0


if __name__ == "__main__":
    sys.exit(main())
