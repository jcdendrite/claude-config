"""Tests for restore-authorization-boundary-on-compact.sh.

The hook is a SessionStart hook (matcher: compact) that emits
hookSpecificOutput.additionalContext restating the irreversible-action
authorization boundary — the harness-generated compact summary carries no
trace of it, and its final "Optional Next Step" section names no distinction
between a reversible step and one that merges a PR, force-pushes, or deletes
in bulk.

Output is emitted only when `.source == "compact"`; every other source
(including a missing/non-string one) produces no output. This is advisory
context, not a gate: it cannot be used with run_hook_session_start, which
asserts the exact {"hookEventName", "sessionTitle"} key set a sessionTitle
hook emits.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, build_path_without

RESTORE_BOUNDARY_HOOK = HOOKS_DIR / "restore-authorization-boundary-on-compact.sh"
_REPO_ROOT = Path(__file__).resolve().parents[4]
_HANDOFF_SKILL = _REPO_ROOT / "claude" / ".claude" / "skills" / "handoff" / "SKILL.md"

# Literal command/verb tokens the hook's injected text names — anchored on
# the literal token rather than a descriptive clause so a prose reword does
# not false-fail this suite.
_NAMED_TOKENS = [
    "gh pr close",
    "git branch -d",
    "gh release create",
    "git push --force",
    "rm -rf",
]


def _run_hook(
    payload: dict | None,
    isolated_home: Path,
    extra_env: dict | None = None,
    raw_stdin: bytes | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(isolated_home)}
    if extra_env:
        env.update(extra_env)
    stdin = raw_stdin if raw_stdin is not None else json.dumps(payload).encode()
    return subprocess.run(
        [str(RESTORE_BOUNDARY_HOOK)],
        input=stdin,
        capture_output=True,
        env=env,
        check=False,
    )


def _additional_context(result: subprocess.CompletedProcess) -> str:
    """Parse hookSpecificOutput.additionalContext from the hook's JSON output."""
    return json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]


class TestRestoreAuthorizationBoundaryOnCompact:
    def test_compact_source_emits_valid_json_with_principle_clause(self, isolated_home):
        """source: compact -> valid JSON payload whose additionalContext
        states the summary is a reconstruction, not authorization."""
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0
        parsed = json.loads(result.stdout)
        hook_output = parsed["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "SessionStart"
        ctx = hook_output["additionalContext"]
        assert isinstance(ctx, str)
        assert "not engineer authorization" in ctx

    @pytest.mark.parametrize("token", _NAMED_TOKENS)
    def test_named_shape_token_present(self, isolated_home, token):
        """Each illustrative shape is named by its literal command/verb
        token, not only a descriptive clause."""
        result = _run_hook({"source": "compact"}, isolated_home)
        ctx = _additional_context(result)
        assert token in ctx

    def test_gh_pr_merge_not_named(self, isolated_home):
        """gh pr merge is unconditionally gated by block-gh-pr-merge.sh, so
        this hook must not name it — a future edit pasting handoff/SKILL.md
        §3.5's full list in would violate that invariant silently without
        this assertion."""
        result = _run_hook({"source": "compact"}, isolated_home)
        ctx = _additional_context(result)
        assert "gh pr merge" not in ctx

    def test_named_tokens_are_subset_of_handoff_skill_section_3_5(self, isolated_home):
        """Every command token the hook names must appear somewhere in
        handoff/SKILL.md's §3.5 categorization list — a subset check,
        deliberately not equality, since §3.5 may gain a shape that IS
        gated, which the hook should then correctly omit."""
        section_3_5 = _HANDOFF_SKILL.read_text()
        match = re.search(r"## §3\.5.*?(?=\n## §4)", section_3_5, re.DOTALL)
        assert match, "handoff/SKILL.md §3.5 section not found — has it been renamed?"
        section_3_5_text = match.group(0)
        for token in _NAMED_TOKENS:
            assert token in section_3_5_text, (
                f"hook names {token!r}, but it does not appear in handoff/SKILL.md §3.5 — "
                "the two lists have drifted apart"
            )

    @pytest.mark.parametrize("source", ["startup", "clear", "resume", "fork"])
    def test_non_compact_source_produces_no_output(self, isolated_home, source):
        """Every source other than compact stays silent — this covers the
        in-script filter independently of the settings.json matcher."""
        result = _run_hook({"source": source}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_source_produces_no_output(self, isolated_home):
        result = _run_hook({}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    @pytest.mark.parametrize("bad_source", [123, True, {"nested": "compact"}])
    def test_non_string_source_produces_no_output(self, isolated_home, bad_source):
        """A .source that parses but isn't a string (number, bool, object)
        is a distinct code path from malformed stdin — both must stay silent."""
        result = _run_hook({"source": bad_source}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_kill_switch_present_produces_no_output(self, isolated_home):
        marker_dir = isolated_home / ".claude"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / ".authorization-boundary-disabled").touch()
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0
        assert result.stdout == b""

    def test_kill_switch_under_config_dir_produces_no_output(self, isolated_home, tmp_path):
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        (config_dir / ".authorization-boundary-disabled").touch()
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_legacy_home_kill_switch_ignored_when_config_dir_set(self, isolated_home, tmp_path):
        """Config-dir resolution is a swap, not a union: a kill switch at the
        legacy $HOME/.claude location produces no suppression once
        CLAUDE_CONFIG_DIR points elsewhere — the boundary text still emits."""
        marker_dir = isolated_home / ".claude"
        marker_dir.mkdir(parents=True, exist_ok=True)
        (marker_dir / ".authorization-boundary-disabled").touch()
        config_dir = tmp_path / "profile"
        config_dir.mkdir(parents=True)
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        ctx = _additional_context(result)
        assert "not engineer authorization" in ctx

    def test_malformed_stdin_exits_0_with_no_output(self, isolated_home):
        result = _run_hook(None, isolated_home, raw_stdin=b"not json")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_empty_stdin_exits_0_with_no_output(self, isolated_home):
        result = _run_hook(None, isolated_home, raw_stdin=b"")
        assert result.returncode == 0
        assert result.stdout == b""

    def test_missing_jq_exits_0_with_no_output(self, isolated_home, tmp_path):
        """A hook that cannot encode its output must fail open, not block
        session startup."""
        farm_dir = tmp_path / "path_without_jq"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        result = _run_hook(
            {"source": "compact"}, isolated_home, extra_env={"PATH": path_without_jq}
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_unresolvable_config_dir_exits_0_with_no_output(self, isolated_home):
        """A relative CLAUDE_CONFIG_DIR cannot be resolved to a kill-switch
        location, so the hook must fail open rather than probe a
        root-anchored path."""
        result = _run_hook(
            {"source": "compact"},
            isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 0
        assert result.stdout == b""

    def test_exit_0_always(self, isolated_home):
        result = _run_hook({"source": "compact"}, isolated_home)
        assert result.returncode == 0
