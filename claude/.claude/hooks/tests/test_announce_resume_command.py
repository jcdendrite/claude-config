"""Tests for announce-resume-command.sh."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    assert_cap_engaged,
    edit_input,
    multiedit_input,
    read_input,
    write_input,
)

ANNOUNCE_HOOK = HOOKS_DIR / "announce-resume-command.sh"
_SETTINGS_PATH = HOOKS_DIR.parent / "settings.json"


def _registered_post_tool_use_event_name() -> str:
    """The hookEventName this hook's emission must claim, derived from
    settings.json rather than hardcoded — mirrors
    test_consume_durable_continuity_file_on_read.py's helper of the same
    name, since a divergence here silently drops
    hookSpecificOutput.additionalContext the same way."""
    settings = json.loads(_SETTINGS_PATH.read_text())
    for event_name, groups in settings["hooks"].items():
        for group in groups:
            for entry in group.get("hooks", []):
                if entry.get("command", "").endswith(ANNOUNCE_HOOK.name):
                    return event_name
    raise AssertionError(f"{ANNOUNCE_HOOK.name} not found in {_SETTINGS_PATH}")


def _run_hook_raw(
    hook: Path, tool_input: dict, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Like helpers.run_hook, but returns the raw CompletedProcess instead of
    the decoded permissionDecision — needed for tests asserting on the
    `systemMessage` JSON this hook emits, which run_hook's decision-decoding
    doesn't expose."""
    env = dict(os.environ)
    env["HOME"] = str(home)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )


def _write_fixture(base: Path, rel_path: str) -> Path:
    path = base / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture content\n")
    return path


def _stub_git_linked_worktree(tmp_path: Path, toplevel: str) -> tuple[dict, Path]:
    """(PATH env, stub bin dir) for a `git` stub that reports a linked
    worktree (differing git-dir and common-dir) whose --show-toplevel prints
    `toplevel` verbatim. The hook discards git's stderr, so the stub records
    its behavior in marker files under the bin dir instead.

    Supported git argv forms: `--git-dir`, `--absolute-git-dir`,
    `--path-format=absolute --git-common-dir`, and `--show-toplevel`. The
    stub ignores the value of any `-C <dir>`.

    - `show-toplevel-called` is touched when the toplevel call is reached.
    - `unexpected-argv` collects any other git argv (the stub then exits 99)."""
    stub_bin = tmp_path / "stub-bin"
    stub_bin.mkdir()
    fake_git = stub_bin / "git"
    fake_git.write_text(
        "#!/bin/bash\n"
        'if [ "$3" = "rev-parse" ]; then\n'
        '  case "$4" in\n'
        "    --git-dir) exit 0 ;;\n"
        "    --absolute-git-dir) printf '%s\\n' /fake/git-dir/A; exit 0 ;;\n"
        "    --path-format=absolute)\n"
        '      if [ "$5" = "--git-common-dir" ]; then\n'
        "        printf '%s\\n' /fake/git-dir/B; exit 0\n"
        "      fi\n"
        "      ;;\n"
        "    --show-toplevel)\n"
        f"      touch {shlex.quote(str(stub_bin / 'show-toplevel-called'))}\n"
        f"      printf '%s' {shlex.quote(toplevel)}; exit 0 ;;\n"
        "  esac\n"
        "fi\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(stub_bin / 'unexpected-argv'))}\n"
        "exit 99\n"
    )
    fake_git.chmod(0o755)
    return {"PATH": f"{stub_bin}:{os.environ['PATH']}"}, stub_bin


@pytest.fixture
def linked_worktree(git_repo, tmp_path):
    """A linked worktree of git_repo, checked out on a fresh branch — the
    payload `.cwd` used to exercise the `--cwd`-included branch of the
    git-dir comparison."""
    worktree = tmp_path / "linked-worktree"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "wt-branch", str(worktree)],
        cwd=git_repo,
        check=True,
    )
    return worktree


class TestAnnounceResumeCommand:
    # -----------------------------------------------------------------------
    # Root-cause regression: config-dir resolution
    # -----------------------------------------------------------------------

    def test_announces_command_naming_non_default_config_dir(self, isolated_home, tmp_path):
        """A CLAUDE_CONFIG_DIR that is not $HOME/.claude must be reflected in
        the announced command — a hook that resolved the wrong config dir
        would still pass every $HOME/.claude case."""
        config_dir = tmp_path / "custom-profile"
        fixture = _write_fixture(config_dir, "handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture)),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert str(config_dir) in payload["systemMessage"]
        assert "resume-context" in payload["systemMessage"]

    def test_announces_command_for_brief_arm(self, isolated_home):
        """The checker-less sibling (briefs/*-task.md, with no check-brief.py
        counterpart) is pinned directly, not merely assumed to work because
        the handoff arm does."""
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert str(fixture) in payload["systemMessage"]
        assert "resume-context" in payload["systemMessage"]

    # -----------------------------------------------------------------------
    # Fires on every file-writing tool
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "make_input", [edit_input, write_input, multiedit_input], ids=["Edit", "Write", "MultiEdit"]
    )
    def test_fires_on_every_file_writing_tool(self, isolated_home, make_input):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, make_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "resume-context" in payload["systemMessage"]

    # -----------------------------------------------------------------------
    # Path/tool_name filtering (defense-in-depth)
    # -----------------------------------------------------------------------

    @pytest.mark.parametrize(
        "rel_path",
        [".claude/handoffs/notes.md", "elsewhere/example-handoff.md"],
        ids=["wrong-suffix-same-dir", "outside-config-dir"],
    )
    def test_non_matching_path_emits_nothing(self, isolated_home, rel_path):
        fixture = _write_fixture(isolated_home, rel_path)
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_matching_path_wrong_tool_name_emits_nothing(self, isolated_home):
        """Defense-in-depth: filters tool_name itself, independent of the
        settings.json matcher condition."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, read_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    def test_tool_input_missing_file_path_key_emits_nothing(self, isolated_home):
        """tool_input carries no file_path key at all. This converges on the
        same exit-0/no-stdout outcome as a path-glob mismatch (a missing key
        resolves to the literal string "null", which fails the glob), so it
        does not independently pin the `// empty` jq fallback -- it only
        pins that this input shape is still handled without error."""
        payload = write_input(str(isolated_home / ".claude" / "handoffs" / "example-handoff.md"))
        del payload["tool_input"]["file_path"]
        result = _run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""

    # -----------------------------------------------------------------------
    # --cwd branch
    # -----------------------------------------------------------------------

    def test_linked_worktree_cwd_includes_cwd_flag(self, isolated_home, linked_worktree):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(linked_worktree)),
            home=isolated_home,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert f"--cwd {linked_worktree}" in payload["systemMessage"]
        assert f"--cwd {linked_worktree}" in payload["hookSpecificOutput"]["additionalContext"]

    def test_main_tree_cwd_omits_cwd_flag(self, isolated_home, git_repo):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(git_repo)),
            home=isolated_home,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert "resume-context" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert "resume-context" in payload["hookSpecificOutput"]["additionalContext"]

    def test_cwd_absent_omits_cwd_flag_but_still_announces(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert "resume-context" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert "resume-context" in payload["hookSpecificOutput"]["additionalContext"]

    @pytest.mark.timing
    def test_git_dir_check_timeout_falls_back_to_bare_command(
        self, isolated_home, linked_worktree, git_timeout_shim, tmp_path
    ):
        """_lib_capped's 5s cap on the git-dir comparison is actually
        exercised, not merely present in the code: a hung `git -C ... rev-
        parse --git-dir` must not hang the hook, and the capped-off branch
        must fall back to the bare (no --cwd) resume command rather than
        erroring."""
        env = git_timeout_shim('[ "$3" = "rev-parse" ] && [ "$4" = "--git-dir" ]')
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        with assert_cap_engaged(tmp_path, production_cap=5):
            result = _run_hook_raw(
                ANNOUNCE_HOOK,
                write_input(str(fixture), cwd=str(linked_worktree)),
                home=isolated_home,
                extra_env=env,
            )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert f"resume-context {fixture}" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert f"resume-context {fixture}" in payload["hookSpecificOutput"]["additionalContext"]

    def test_cwd_naming_non_repo_dir_omits_cwd_flag(self, isolated_home, tmp_path):
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(non_repo)),
            home=isolated_home,
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert "resume-context" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert "resume-context" in payload["hookSpecificOutput"]["additionalContext"]

    # -----------------------------------------------------------------------
    # Allowlist gate on the interpolated file_path
    # -----------------------------------------------------------------------

    def test_clean_file_path_passes_allowlist(self, isolated_home):
        """FILE_PATH allowlist gate, allow branch: a path built only from allowlisted bytes is announced."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert f"resume-context {fixture}" in payload["systemMessage"]

    def test_file_path_with_embedded_newline_emits_nothing(self, isolated_home):
        """Bash `case` globs match across embedded newlines, so this path
        clears the continuity-path glob and must be caught by the allowlist
        gate instead — the adversarial case that would otherwise leak an
        injected sentinel into model-visible additionalContext."""
        handoffs_dir = isolated_home / ".claude" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        malicious_name = "notes\n\nSENTINEL-INJECT\n\nx-handoff.md"
        fixture = handoffs_dir / malicious_name
        fixture.write_text("fixture content\n")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        assert result.stdout == ""
        assert "SENTINEL-INJECT" not in result.stdout

    @pytest.mark.parametrize(
        ("raw_name", "stripped_name"),
        [("a\u0000b-handoff.md", "ab-handoff.md"), ("a-handoff.md\n", "a-handoff.md")],
        ids=["embedded-nul", "trailing-newline"],
    )
    def test_file_path_bytes_dropped_by_shell_announce_only_allowlisted_bytes(
        self, isolated_home, raw_name, stripped_name
    ):
        """Residual of the allowlist gate: a NUL or trailing newline in the JSON
        file_path is dropped by the shell's command substitution before the
        gate runs, so the announced command carries only allowlisted bytes and
        the stripped path."""
        stripped_path = _write_fixture(isolated_home, f".claude/handoffs/{stripped_name}")
        raw_path = f"{stripped_path.parent}/{raw_name}"
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(raw_path), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        message = payload["systemMessage"]
        assert f"resume-context {stripped_path}" in message
        additional_context = payload["hookSpecificOutput"]["additionalContext"]
        assert "\x00" not in message
        assert "\x00" not in additional_context
        assert "\n" not in message
        assert "\n" not in additional_context

    def test_worktree_root_with_embedded_newline_falls_back_to_bare_command(
        self, isolated_home, tmp_path
    ):
        """The WORKTREE_ROOT gate's counterpart to
        test_file_path_with_embedded_newline_emits_nothing: a worktree root
        containing an embedded newline fails the allowlist, so only --cwd is
        dropped and none of the root's text, including any injected sentinel,
        reaches the output.

        Uses a PATH-stubbed git because the CANDIDATE_ROOT call site never validates
        the path is a real directory, so it is platform-independent.
        A real `git worktree add` with a newline in the path depends on the local git
        accepting such paths, so the stub avoids that dependency."""
        malicious_root = "linked\n\nSENTINEL-INJECT\n\nwt"
        stub_env, stub_bin = _stub_git_linked_worktree(tmp_path, malicious_root)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(isolated_home)),
            home=isolated_home,
            extra_env=stub_env,
        )
        assert result.returncode == 0
        assert not (stub_bin / "unexpected-argv").exists()
        assert (stub_bin / "show-toplevel-called").exists()
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert f"resume-context {fixture}" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert f"resume-context {fixture}" in payload["hookSpecificOutput"]["additionalContext"]
        assert "SENTINEL-INJECT" not in result.stdout

    def test_clean_worktree_root_from_stub_git_emits_cwd_flag(self, isolated_home, tmp_path):
        """Positive control for the stub-git newline test above: the same stub
        with an allowlist-clean toplevel emits --cwd, proving the toplevel
        call is reached."""
        clean_root = "/fake/worktrees/linked-root"
        stub_env, stub_bin = _stub_git_linked_worktree(tmp_path, clean_root)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(isolated_home)),
            home=isolated_home,
            extra_env=stub_env,
        )
        assert result.returncode == 0
        assert not (stub_bin / "unexpected-argv").exists()
        payload = json.loads(result.stdout)
        assert f"resume-context --cwd {clean_root} {fixture}" in payload["systemMessage"]

    # -----------------------------------------------------------------------
    # Emitted contract shape
    # -----------------------------------------------------------------------

    def test_hook_event_name_matches_registration(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert payload["hookSpecificOutput"]["hookEventName"] == _registered_post_tool_use_event_name()

    # -----------------------------------------------------------------------
    # Fail-open paths
    # -----------------------------------------------------------------------

    def test_empty_stdin_emits_nothing(self, isolated_home):
        env = dict(os.environ)
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(ANNOUNCE_HOOK)], input="", capture_output=True, text=True, env=env, check=False
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_malformed_json_emits_nothing(self, isolated_home):
        env = dict(os.environ)
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(ANNOUNCE_HOOK)],
            input="not valid json {{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_unreadable_lib_sh_is_silent(self, isolated_home, tmp_path):
        """dirname($0) resolves to HOOKS_DIR only when the hook runs from its
        real location; running a copy with no adjacent _lib.sh exercises the
        "could not source _lib.sh" exit-0 path directly."""
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_hook = Path(tmpdir) / ANNOUNCE_HOOK.name
            shutil.copy2(ANNOUNCE_HOOK, tmp_hook)
            tmp_hook.chmod(0o755)
            result = subprocess.run(
                ["bash", str(tmp_hook)],
                input=json.dumps(write_input(str(fixture))),
                cwd=str(tmp_path),
                env={**os.environ, "HOME": str(isolated_home)},
                capture_output=True,
                text=True,
                check=False,
            )
        assert result.returncode == 0
        assert result.stdout.strip() == ""

    def test_non_matching_path_never_invokes_git(self, isolated_home, tmp_path):
        """Pins that the path glob gates the git calls, not just the emitted
        output — a future reorder that moved the git calls ahead of the glob
        would fail this test rather than passing silently."""
        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        marker = stub_bin / "git-invoked"
        fake_git = stub_bin / "git"
        fake_git.write_text(f"#!/bin/bash\ntouch {shlex.quote(str(marker))}\nexit 1\n")
        fake_git.chmod(0o755)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/notes.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(isolated_home)),
            home=isolated_home,
            extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert not marker.exists(), "git must not be invoked before the path glob matches"

    def test_allowlist_failing_path_never_invokes_git(self, isolated_home, tmp_path):
        """Mirrors test_non_matching_path_never_invokes_git above, but for a
        path that clears the continuity-file glob and fails the FILE_PATH
        allowlist instead of never matching the glob at all — the
        allowlist gate must also run ahead of the git calls, not just the
        glob."""
        stub_bin = tmp_path / "stub-bin"
        stub_bin.mkdir()
        marker = stub_bin / "git-invoked"
        fake_git = stub_bin / "git"
        fake_git.write_text(f"#!/bin/bash\ntouch {shlex.quote(str(marker))}\nexit 1\n")
        fake_git.chmod(0o755)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/my notes-handoff.md")
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(isolated_home)),
            home=isolated_home,
            extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}"},
        )
        assert result.returncode == 0
        assert result.stdout == ""
        assert not marker.exists(), "git must not be invoked when FILE_PATH fails the allowlist"
