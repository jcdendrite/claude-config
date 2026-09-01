"""Tests for require-memory-skill.sh."""
from __future__ import annotations

import os
import subprocess
import textwrap
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SKILLS_DIR,
    assert_gate_handles_traversal_session_id,
    bash_input,
    build_no_realpath_m_path_env,
    edit_input,
    extract_skill_command,
    multiedit_input,
    run_hook,
    run_hook_reason,
    run_skill_command,
    write_input,
)

from .conftest import _seed_session

HOOK_PATH = HOOKS_DIR / "require-memory-skill.sh"
AI_MEMORY_SKILL = SKILLS_DIR / "ai-instruction-and-memory-files" / "SKILL.md"

# Forces _lib_realpath_m's manual fallback branch by shadowing both native
# `realpath -m` and `grealpath` on PATH -- mirrors test_lib.py's
# _FORCED_FALLBACK_REALPATH_SHIM, prepended rather than substituted for PATH
# so jq/git/sha256sum/timeout stay resolvable for the rest of the hook.
_FORCED_FALLBACK_REALPATH_SHIM = textwrap.dedent("""\
    #!/bin/bash
    if [ "$1" = "-m" ]; then
      echo "realpath: illegal option -- m" >&2
      exit 1
    fi
    exec /bin/realpath "$@"
""")
_FORCED_FALLBACK_GREALPATH_SHIM = textwrap.dedent("""\
    #!/bin/bash
    exit 1
""")


@pytest.fixture
def memory_tree(isolated_home):
    """Populate a realistic auto-memory directory under the isolated $HOME.

    Creates:
      ~/.claude/projects/abc123/memory/MEMORY.md  (existing index)
      ~/.claude/projects/abc123/memory/user_role.md  (existing topic file)

    Returns the path to the memory directory.
    """
    mem_dir = isolated_home / ".claude" / "projects" / "abc123" / "memory"
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").touch()
    (mem_dir / "user_role.md").write_text("# User role\n")
    return mem_dir


def _memory_input(base_input: dict, session_id: str) -> dict:
    """Merge session_id into an existing tool-input dict."""
    return {**base_input, "session_id": session_id}


def _write_active_marker(isolated_home: Path, session_id: str) -> Path:
    """Create an active-bypass marker with the current process PID (alive)."""
    marker_dir = isolated_home / ".claude" / ".memory-skill-active.d"
    marker_dir.mkdir(parents=True, exist_ok=True)
    marker = marker_dir / session_id
    marker.write_text(str(os.getpid()))
    return marker


class TestRequireMemorySkill:
    def test_memory_md_edit_blocked(self, isolated_home, memory_tree):
        """Edit on MEMORY.md is denied (no active marker for this session)."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-edit")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_write_blocked(self, isolated_home, memory_tree):
        """Write to MEMORY.md is denied."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(write_input(memory_md), "sess-write")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_memory_md_multiedit_blocked(self, isolated_home, memory_tree):
        """MultiEdit on MEMORY.md is denied."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(multiedit_input(memory_md), "sess-multiedit")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_new_topic_file_write_blocked(self, isolated_home, memory_tree):
        """Write to a non-existent path under memory/ is denied (new topic file)."""
        new_topic = str(memory_tree / "new_topic.md")
        assert not Path(new_topic).exists()
        payload = _memory_input(write_input(new_topic), "sess-new-topic")
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_unresolvable_new_topic_file_path_denied(
        self, isolated_home, memory_tree, tmp_path
    ):
        """A new-topic-file path _lib_realpath_m cannot resolve (its fallback
        loop's depth cap exhausted, e.g. on a system lacking both
        realpath -m and grealpath) must still be denied -- an unresolved
        path must not be read as proof the target isn't a memory file and
        let the gate skip."""
        deep_target = memory_tree
        for i in range(11):
            deep_target = deep_target / f"lvl{i}"
        assert not deep_target.exists()
        payload = _memory_input(write_input(str(deep_target)), "sess-unresolvable")
        decision = run_hook(
            HOOK_PATH,
            payload,
            extra_env={"PATH": build_no_realpath_m_path_env(tmp_path)},
        )
        assert decision == "deny"

    def test_existing_topic_file_edit_allowed(self, isolated_home, memory_tree):
        """Edit on an existing topic file passes through (only new files are gated)."""
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(edit_input(existing), "sess-existing-edit")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_existing_topic_file_write_allowed(self, isolated_home, memory_tree):
        """Write overwriting an existing topic file passes through."""
        existing = str(memory_tree / "user_role.md")
        payload = _memory_input(write_input(existing), "sess-existing-write")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_non_memory_path_allowed(self, isolated_home):
        """Edit on a path outside the memory directory passes through."""
        readme = str(isolated_home / "some-project" / "README.md")
        payload = _memory_input(edit_input(readme), "sess-non-memory")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_memory_md_edit_blocked_through_symlinked_projects_dir(
        self, isolated_home, tmp_path
    ):
        """projects/ symlinked independently of its $HOME/.claude parent
        must still resolve to the same physical prefix as the target path,
        so an Edit reached through the symlink is classified and denied."""
        real_projects = tmp_path / "real-projects-elsewhere"
        mem_dir = real_projects / "abc123" / "memory"
        mem_dir.mkdir(parents=True)
        (mem_dir / "MEMORY.md").touch()

        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "projects").symlink_to(real_projects)

        memory_md_via_symlink = claude_dir / "projects" / "abc123" / "memory" / "MEMORY.md"
        payload = _memory_input(
            edit_input(str(memory_md_via_symlink)), "sess-symlinked-projects-edit"
        )
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_new_topic_file_blocked_through_symlinked_projects_dir(
        self, isolated_home, tmp_path
    ):
        """Same symlinked-projects/ scenario as above, for the new-topic-file
        (class b) path rather than the MEMORY.md-index (class a) path."""
        real_projects = tmp_path / "real-projects-elsewhere"
        mem_dir = real_projects / "abc123" / "memory"
        mem_dir.mkdir(parents=True)

        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "projects").symlink_to(real_projects)

        new_topic_via_symlink = claude_dir / "projects" / "abc123" / "memory" / "new_topic.md"
        assert not new_topic_via_symlink.exists()
        payload = _memory_input(
            write_input(str(new_topic_via_symlink)), "sess-symlinked-projects-write"
        )
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_existing_topic_file_edit_allowed_through_symlinked_projects_dir(
        self, isolated_home, tmp_path
    ):
        """Allow-path counterpart to the two deny tests above: an Edit on an
        already-existing topic file reached through the symlinked projects/
        still passes through, pairing with test_existing_topic_file_edit_allowed
        so a future over-broadened prefix match would show up here too."""
        real_projects = tmp_path / "real-projects-elsewhere"
        mem_dir = real_projects / "abc123" / "memory"
        mem_dir.mkdir(parents=True)
        existing = mem_dir / "user_role.md"
        existing.write_text("# User role\n")

        claude_dir = isolated_home / ".claude"
        claude_dir.mkdir(parents=True, exist_ok=True)
        (claude_dir / "projects").symlink_to(real_projects)

        existing_via_symlink = claude_dir / "projects" / "abc123" / "memory" / "user_role.md"
        payload = _memory_input(
            edit_input(str(existing_via_symlink)), "sess-symlinked-projects-allow"
        )
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_memory_md_dangling_symlink_denies_under_forced_realpath_fallback(
        self, isolated_home, memory_tree, tmp_path
    ):
        """A MEMORY.md path that is itself an undereferenceable dangling
        symlink must still be gated when _lib_realpath_m's manual fallback
        fails to resolve it (neither native `realpath -m` nor `grealpath`
        on PATH) -- mirrors require-plan-review.sh's
        test_symlinked_plan_file_denies_under_forced_realpath_fallback.
        Before REAL_PATH_STATUS was checked, an empty REAL_PATH matched
        neither classification pattern and the write fell through to a
        silent allow."""
        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        (shim_dir / "realpath").write_text(_FORCED_FALLBACK_REALPATH_SHIM)
        (shim_dir / "realpath").chmod(0o755)
        (shim_dir / "grealpath").write_text(_FORCED_FALLBACK_GREALPATH_SHIM)
        (shim_dir / "grealpath").chmod(0o755)

        memory_md = memory_tree / "MEMORY.md"
        memory_md.unlink()
        memory_md.symlink_to(tmp_path / "outside" / "nonexistent-target")

        payload = _memory_input(write_input(str(memory_md)), "sess-dangling-symlink-fallback")
        assert (
            run_hook(
                HOOK_PATH,
                payload,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    def test_dangling_symlink_outside_memory_tree_denies_under_forced_realpath_fallback(
        self, isolated_home, tmp_path
    ):
        """A dangling-symlink write target with no relationship to the
        memory tree is still denied when _lib_realpath_m's fallback also
        fails to resolve it. This is an accepted over-broad-gating
        trade-off, not a bug: a raw-path-shape narrowing was tried and
        reverted after it let an obfuscated-but-real memory path (one
        containing a literal ".." that trips the fallback loop, e.g.
        "$CONFIG_DIR/decoy/../projects/abc123/memory/f.md") slip through as an
        allow, because the raw string didn't start with the expected
        literal prefix even though it denoted a genuine memory-tree
        location. Once resolution has failed there is no reliable way to
        distinguish that case from a truly unrelated dangling symlink, so
        every resolution failure is gated."""
        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        (shim_dir / "realpath").write_text(_FORCED_FALLBACK_REALPATH_SHIM)
        (shim_dir / "realpath").chmod(0o755)
        (shim_dir / "grealpath").write_text(_FORCED_FALLBACK_GREALPATH_SHIM)
        (shim_dir / "grealpath").chmod(0o755)

        unrelated_dir = tmp_path / "probe_project_dir"
        unrelated_dir.mkdir()
        unrelated_symlink = unrelated_dir / "unrelated-file.py"
        unrelated_symlink.symlink_to(tmp_path / "outside" / "nonexistent-target")

        payload = _memory_input(
            write_input(str(unrelated_symlink)), "sess-unrelated-dangling-symlink"
        )
        assert (
            run_hook(
                HOOK_PATH,
                payload,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    def test_obfuscated_memory_path_denied_under_forced_realpath_fallback(
        self, isolated_home, memory_tree, tmp_path
    ):
        """A new-topic-file path that both (a) fails resolution under the
        forced-fallback environment (the manual fallback loop returns
        failure on encountering a literal ".." path component) and (b)
        denotes a genuine memory-tree location once ".." is collapsed, but
        whose raw spelling does not start with the literal
        "$CONFIG_DIR/projects/" prefix, is still denied -- pins the
        classification bypass a raw-path-shape narrowing previously
        introduced and this suite's sibling test above documents as an
        accepted trade-off."""
        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        (shim_dir / "realpath").write_text(_FORCED_FALLBACK_REALPATH_SHIM)
        (shim_dir / "realpath").chmod(0o755)
        (shim_dir / "grealpath").write_text(_FORCED_FALLBACK_GREALPATH_SHIM)
        (shim_dir / "grealpath").chmod(0o755)

        obfuscated_target = (
            isolated_home
            / ".claude"
            / "decoy"
            / ".."
            / "projects"
            / "abc123"
            / "memory"
            / "newtopic.md"
        )

        payload = _memory_input(
            write_input(str(obfuscated_target)), "sess-obfuscated-memory-path"
        )
        assert (
            run_hook(
                HOOK_PATH,
                payload,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "deny"
        )

    def test_memory_md_dangling_symlink_allows_with_active_marker_under_forced_realpath_fallback(
        self, isolated_home, memory_tree, tmp_path
    ):
        """Allow-side companion to
        test_memory_md_dangling_symlink_denies_under_forced_realpath_fallback:
        the same dangling-symlink MEMORY.md target, reclassified as
        IS_CANDIDATE=1 via the resolution-failure branch, must still allow
        through when the active-bypass marker is live -- the legitimate
        in-progress memory-write session this fail-closed branch must not
        also block."""
        shim_dir = tmp_path / "realpath_shim"
        shim_dir.mkdir()
        (shim_dir / "realpath").write_text(_FORCED_FALLBACK_REALPATH_SHIM)
        (shim_dir / "realpath").chmod(0o755)
        (shim_dir / "grealpath").write_text(_FORCED_FALLBACK_GREALPATH_SHIM)
        (shim_dir / "grealpath").chmod(0o755)

        memory_md = memory_tree / "MEMORY.md"
        memory_md.unlink()
        memory_md.symlink_to(tmp_path / "outside" / "nonexistent-target")

        sid = "sess-dangling-symlink-fallback-active-marker"
        _write_active_marker(isolated_home, sid)

        payload = _memory_input(write_input(str(memory_md)), sid)
        assert (
            run_hook(
                HOOK_PATH,
                payload,
                extra_env={"PATH": f"{shim_dir}{os.pathsep}{os.environ['PATH']}"},
            )
            == "allow"
        )

    def test_memory_md_edit_blocked_under_claude_config_dir(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR-set: a MEMORY.md write under
        $CLAUDE_CONFIG_DIR/projects/.../memory/ is classified as a candidate
        and denied without an active-bypass marker."""
        profile_dir = tmp_path / "profile"
        mem_dir = profile_dir / "projects" / "abc123" / "memory"
        mem_dir.mkdir(parents=True)
        memory_md = mem_dir / "MEMORY.md"
        memory_md.touch()
        payload = _memory_input(edit_input(str(memory_md)), "sess-config-dir-edit")
        decision = run_hook(
            HOOK_PATH,
            payload,
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(profile_dir)},
        )
        assert decision == "deny"

    def test_unresolvable_config_dir_allows_through(self):
        """Ledger row 6 fail-open regression: $HOME empty and
        CLAUDE_CONFIG_DIR unset means _lib_config_dir cannot resolve, so no
        path is classified as a memory-file candidate and the gate allows
        the write through instead of blocking every Write/Edit/MultiEdit."""
        import json

        env = dict(os.environ)
        env.pop("CLAUDE_CONFIG_DIR", None)
        env["HOME"] = ""
        payload = _memory_input(
            edit_input("/some/projects/abc123/memory/MEMORY.md"),
            "sess-no-config-dir",
        )
        result = subprocess.run(
            [str(HOOK_PATH)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert not result.stdout.strip()

    def test_empty_json_object_denied(self):
        """'{}' → DENY (empty TOOL_NAME; path c: no .tool_name in payload)."""
        payload = {}
        assert run_hook(HOOK_PATH, payload) == "deny"

    def test_bash_tool_allowed(self, isolated_home):
        """Bash input passes through — self-filter by tool name."""
        payload = bash_input("echo hello", session_id="sess-bash")
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_missing_session_id_allows(self, isolated_home, memory_tree):
        """Edit on MEMORY.md with no session_id in input passes through (fail open)."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = edit_input(memory_md)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_malformed_json_stdin(self, isolated_home):
        """Malformed JSON stdin must fail closed (deny), not silently allow."""
        import json
        result = subprocess.run(
            [str(HOOK_PATH)],
            input="not-valid-json{{{",
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip(), "Hook must emit a deny message on malformed JSON, not silent exit"
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_deny_message_mentions_skill_name(self, isolated_home, memory_tree):
        """Deny reason must reference ai-instruction-and-memory-files."""
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-reason")
        reason = run_hook_reason(HOOK_PATH, payload)
        assert reason is not None
        assert "ai-instruction-and-memory-files" in reason

    # -- Active-marker bypass tests ------------------------------------------

    def test_active_marker_present_allows(self, isolated_home, memory_tree):
        """Fresh active-bypass marker allows Edit on MEMORY.md through."""
        sid = "sess-active-allow"
        _write_active_marker(isolated_home, sid)
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), sid)
        assert run_hook(HOOK_PATH, payload) == "allow"

    def test_dead_pid_active_marker_evicts_and_denies(self, isolated_home, memory_tree):
        """Orphaned marker with a dead PID is evicted and the gate denies."""
        sid = "sess-dead-pid"
        marker_dir = isolated_home / ".claude" / ".memory-skill-active.d"
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker = marker_dir / sid
        marker.write_text("99999999")  # PID outside Linux/macOS max range → always dead
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), sid)
        assert run_hook(HOOK_PATH, payload) == "deny"
        assert not marker.exists(), "hook must evict the orphan marker on dead PID"

    def test_active_marker_other_session_does_not_bypass(self, isolated_home, memory_tree):
        """Active marker keyed to a different session_id does not bypass this session."""
        _write_active_marker(isolated_home, "sess-other")
        memory_md = str(memory_tree / "MEMORY.md")
        payload = _memory_input(edit_input(memory_md), "sess-current")
        assert run_hook(HOOK_PATH, payload) == "deny"

    # -- Skill ↔ hook alignment -----------------------------------------------
    # Execute the SKILL.md activate / deactivate recipes verbatim. If the skill
    # body drifts from the marker layout require-memory-skill.sh expects, these fail.

    def test_skill_activate_command_creates_bypass_marker(
        self, isolated_home, memory_tree, git_repo
    ):
        """Run the SKILL.md activate-gate recipe and verify the resulting marker
        authorizes a previously-gated Edit on MEMORY.md."""
        sid = "test-session-memory-skill-activate"
        _seed_session(isolated_home, sid)

        memory_md = str(memory_tree / "MEMORY.md")
        assert (
            run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "deny"
        ), "precondition: MEMORY.md edit must be gated before activate runs"

        activate_command = extract_skill_command(AI_MEMORY_SKILL, "activate-gate")
        run_skill_command(activate_command, cwd=git_repo, isolated_home=isolated_home)

        marker = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert marker.exists(), (
            "SKILL.md activate-gate recipe ran but no marker landed at the "
            "path the hook checks — the skill and hook disagree on layout."
        )
        assert run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "allow"

    def test_skill_deactivate_command_removes_bypass_marker(
        self, isolated_home, memory_tree, git_repo
    ):
        """Run activate then deactivate from SKILL.md; verify deactivate removes
        the marker and the hook re-gates subsequent writes."""
        sid = "test-session-memory-skill-deactivate"
        _seed_session(isolated_home, sid)

        activate_command = extract_skill_command(AI_MEMORY_SKILL, "activate-gate")
        run_skill_command(activate_command, cwd=git_repo, isolated_home=isolated_home)
        marker = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert marker.exists(), "activate-gate setup did not create the marker"

        deactivate_command = extract_skill_command(AI_MEMORY_SKILL, "deactivate-gate")
        run_skill_command(deactivate_command, cwd=git_repo, isolated_home=isolated_home)

        assert not marker.exists(), (
            "SKILL.md deactivate-gate recipe ran but the marker is still "
            "present — the skill and hook disagree on the marker path."
        )
        memory_md = str(memory_tree / "MEMORY.md")
        assert run_hook(HOOK_PATH, _memory_input(edit_input(memory_md), sid)) == "deny"

    # -- Hostile session_id ---------------------------------------------------

    def test_traversal_session_id_denies_and_does_not_touch_marker_dir(
        self, isolated_home, memory_tree
    ):
        """A session_id of '../canary' must not read or write through the
        traversal: ACTIVE_MARKER concatenates it into
        .memory-skill-active.d/../canary, which resolves to a file one level
        up ($HOME/.claude/canary). The invalid id must skip the active-marker
        bypass entirely and fall through to the gate's normal deny — not be
        treated as an authorization to allow."""
        memory_md = str(memory_tree / "MEMORY.md")
        assert_gate_handles_traversal_session_id(
            HOOK_PATH,
            lambda sid: _memory_input(edit_input(memory_md), sid),
            isolated_home,
            expected_decision="deny",
        )
