"""Tests for audit-execution-surface.py -- the pure path-list -> stop/
continue predicate behind /review-pr Step 2's passive-execution audit.

`audit_execution_surface()` is a pure function with no I/O, so every case
below calls it directly (imported via importlib, since the module's
filename is not a valid Python identifier) rather than paying a
subprocess-spawn cost per case. TestCliContract shells out to pin the
actual stdin-read / stdout-JSON / exit-code contract that only a real
subprocess invocation can verify.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "audit-execution-surface.py"

_spec = importlib.util.spec_from_file_location("audit_execution_surface", SCRIPT_PATH)
_audit_execution_surface = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_audit_execution_surface)

audit_execution_surface = _audit_execution_surface.audit_execution_surface


def run_cli(paths: list[str]) -> subprocess.CompletedProcess:
    return run_cli_raw(json.dumps(paths))


def run_cli_raw(stdin_text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        input=stdin_text,
        capture_output=True,
        text=True,
        check=False,
    )


class TestAuditExecutionSurfacePureFunction:
    def test_empty_path_list_does_not_stop(self):
        result = audit_execution_surface([])
        assert result == {"stop": False, "matches": []}

    def test_single_hit_stops_and_names_the_path(self):
        result = audit_execution_surface(["src/app.py", ".mcp.json", "README.md"])
        assert result["stop"] is True
        assert [m["path"] for m in result["matches"]] == [".mcp.json"]
        assert "MCP server" in result["matches"][0]["reason"]

    def test_hit_at_the_100_101_truncation_boundary(self):
        """The 100/101 boundary matters for gh's own `files` field truncation
        (see REFERENCES.md) -- this pins that the predicate itself has no
        analogous length limit: a 101-entry list whose only hit sits at
        index 100 (the 101st entry) must still be caught."""
        paths = [f"src/module_{i}.py" for i in range(100)] + [".claude/hooks/evil.sh"]
        assert len(paths) == 101
        result = audit_execution_surface(paths)
        assert result["stop"] is True
        assert [m["path"] for m in result["matches"]] == [".claude/hooks/evil.sh"]

    def test_unicode_filename_with_no_execution_surface_hit_does_not_stop(self):
        result = audit_execution_surface(["docs/über-日本語.md"])
        assert result == {"stop": False, "matches": []}

    def test_unicode_filename_execution_surface_hit_still_matches(self):
        """A non-ASCII directory segment ahead of a gated filename must not
        break the match -- the classifier only inspects the last segment and
        a fixed set of prefixes, not the whole string's encoding."""
        result = audit_execution_surface(["日本語/CLAUDE.md"])
        assert result["stop"] is True
        assert result["matches"][0]["path"] == "日本語/CLAUDE.md"

    def test_case_variant_mcp_json_still_matches(self):
        """A case-insensitive filesystem (macOS default, Windows) resolves
        `.MCP.json` to the same loaded file as `.mcp.json` -- the classifier
        must fold case rather than matching the literal lowercase spelling."""
        result = audit_execution_surface([".MCP.json"])
        assert result["stop"] is True
        assert result["matches"][0]["path"] == ".MCP.json"

    def test_gitattributes_at_any_depth_matches(self):
        result = audit_execution_surface(["packages/api/.gitattributes"])
        assert result["stop"] is True
        assert "filter" in result["matches"][0]["reason"]

    def test_hookspath_target_directory_matches(self):
        result = audit_execution_surface([".husky/pre-commit"])
        assert result["stop"] is True
        assert "core.hooksPath" in result["matches"][0]["reason"]

    def test_claude_hooks_directory_matches_at_any_depth(self):
        result = audit_execution_surface(["project/claude/.claude/hooks/new-gate.sh"])
        assert result["stop"] is True

    def test_claude_agents_directory_matches(self):
        result = audit_execution_surface([".claude/agents/rogue-reviewer.md"])
        assert result["stop"] is True

    def test_claude_settings_json_matches(self):
        result = audit_execution_surface([".claude/settings.json"])
        assert result["stop"] is True

    def test_ordinary_source_files_do_not_match(self):
        result = audit_execution_surface(
            ["src/index.ts", "tests/index.test.ts", "package.json", "README.md"]
        )
        assert result == {"stop": False, "matches": []}

    def test_multiple_hits_all_reported(self):
        result = audit_execution_surface([".mcp.json", "CLAUDE.md", "src/ok.py"])
        assert result["stop"] is True
        assert sorted(m["path"] for m in result["matches"]) == [".mcp.json", "CLAUDE.md"]

    def test_stops_regardless_of_author_standing(self):
        """The predicate takes only a path list -- no author-association or
        cross-repo argument exists for a standing-based gate to special-case
        on. Pins the case a standing-based gate would wave through: a
        same-repo, non-first-time-contributor PR touching `.mcp.json` or
        `.claude/hooks/**` must still stop, because nothing about author
        standing is even visible here to skip on."""
        assert audit_execution_surface([".mcp.json", "src/legit_change.py"])["stop"] is True
        assert audit_execution_surface([".claude/hooks/new-gate.sh", "README.md"])["stop"] is True


class TestAuditExecutionSurfaceCliContract:
    def test_stdout_is_json_and_exit_status_mirrors_stop(self):
        proc = run_cli([".mcp.json"])
        assert proc.returncode == 1
        payload = json.loads(proc.stdout)
        assert payload["stop"] is True

    def test_no_hits_exits_zero(self):
        proc = run_cli(["README.md"])
        assert proc.returncode == 0
        payload = json.loads(proc.stdout)
        assert payload == {"stop": False, "matches": []}

    def test_empty_stdin_is_treated_as_empty_list(self):
        proc = run_cli_raw("")
        assert proc.returncode == 0
        assert json.loads(proc.stdout) == {"stop": False, "matches": []}

    def test_non_json_stdin_exits_2_with_error_payload(self):
        proc = run_cli_raw("not json")
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert "error" in payload

    def test_non_array_stdin_exits_2_with_error_payload(self):
        proc = run_cli_raw(json.dumps({"not": "a list"}))
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert "error" in payload

    def test_array_with_non_string_entry_exits_2(self):
        proc = run_cli_raw('["ok.py", 5]')
        assert proc.returncode == 2
        payload = json.loads(proc.stdout)
        assert "error" in payload
