"""Tests for consume-durable-continuity-file-on-read.sh."""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    agent_input,
    install_resume_context_script,
    read_input,
    run_hook,
    run_hook_advisory,
)

CONSUME_HOOK = HOOKS_DIR / "consume-durable-continuity-file-on-read.sh"
_SETTINGS_PATH = HOOKS_DIR.parent / "settings.json"


def _registered_post_tool_use_event_name() -> str:
    """The hookEventName this hook's emission must claim, derived from
    settings.json rather than hardcoded — a divergence between the emitted
    and registered event name silently drops hookSpecificOutput.additionalContext,
    per CLAUDE.md's discriminator-literal rule."""
    settings = json.loads(_SETTINGS_PATH.read_text())
    for event_name, groups in settings["hooks"].items():
        for group in groups:
            for entry in group.get("hooks", []):
                if entry.get("command", "").endswith(CONSUME_HOOK.name):
                    return event_name
    raise AssertionError(f"{CONSUME_HOOK.name} not found in {_SETTINGS_PATH}")


def _write_fixture(isolated_home: Path, rel_path: str) -> Path:
    path = isolated_home / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("fixture content\n")
    return path


def _install_resume_context_script_at(config_dir: Path) -> Path:
    """Like helpers.install_resume_context_script, but symlinks into an
    arbitrary CONFIG_DIR/scripts/ rather than isolated_home/.claude/scripts/
    — needed for CLAUDE_CONFIG_DIR cases, where CONFIG_DIR is not
    isolated_home/.claude. Also symlinks CONFIG_DIR/hooks/_lib.sh:
    resume-context.sh sources it via a path relative to its own $0, which
    resolves to CONFIG_DIR/hooks/_lib.sh here, not isolated_home's."""
    hooks_dir = config_dir / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    lib_link = hooks_dir / "_lib.sh"
    if not lib_link.exists():
        lib_link.symlink_to(HOOKS_DIR / "_lib.sh")
    scripts_dir = config_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    link = scripts_dir / "resume-context.sh"
    if not link.exists():
        link.symlink_to(SCRIPTS_DIR / "resume-context.sh")
    return link


def _run_hook_raw(
    hook: Path, tool_input: dict, home: Path, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    """Like helpers.run_hook, but returns the raw CompletedProcess instead of
    the decoded permissionDecision — needed for tests asserting on the
    `systemMessage` JSON this hook now emits on a successful consume, which
    run_hook's decision-decoding doesn't expose."""
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


class TestConsumeDurableContinuityFileOnRead:
    def test_read_handoff_file_consumes_it(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()

    @pytest.mark.parametrize(
        "rel_path",
        [".claude/handoffs/example-handoff.md", ".claude/briefs/example-task.md"],
    )
    def test_successful_consume_emits_destination_on_both_channels(
        self, isolated_home, tmp_path, rel_path
    ):
        """A successful consume reports the moved-to destination on two
        channels: systemMessage (user-visible only) and
        hookSpecificOutput.additionalContext (model-visible, next to the
        tool result) — the model only ever sees the second. Parametrized
        over both continuity types so the brief path is asserted, not just
        consumed: the brief case also pins the incidental fix dropping the
        hardcoded "~/.claude/handoffs" wording, which named the wrong
        directory whenever the consumed file was a brief."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, rel_path)
        tmpdir_root = tmp_path / "resume-tmpdir"
        tmpdir_root.mkdir()
        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"RESUME_CONTEXT_TMPDIR": str(tmpdir_root)},
        )
        assert result.returncode == 0
        assert not fixture.exists()
        moved = [p for p in tmpdir_root.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        dest = str(moved[0])
        payload = json.loads(result.stdout)
        assert dest in payload["systemMessage"]
        assert payload["hookSpecificOutput"]["hookEventName"] == _registered_post_tool_use_event_name()
        assert dest in payload["hookSpecificOutput"]["additionalContext"]
        if rel_path.startswith(".claude/briefs/"):
            assert "handoffs" not in payload["systemMessage"]
            assert "handoffs" not in payload["hookSpecificOutput"]["additionalContext"]

    def test_hook_triggered_consume_lands_a_row_in_the_index(self, isolated_home, tmp_path):
        """The hook performs no move of its own -- it delegates to
        resume-context.sh --consume-only, which is where
        record_consumed_destination actually runs. This exercises that
        delegation end to end rather than re-testing the append itself
        (test_resume_context.py already covers the append in isolation)."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        tmpdir_root = tmp_path / "resume-tmpdir"
        tmpdir_root.mkdir()
        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"RESUME_CONTEXT_TMPDIR": str(tmpdir_root)},
        )
        assert result.returncode == 0
        moved = [p for p in tmpdir_root.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        index = tmpdir_root / f"resume-context-index-{os.geteuid()}" / "consumed.tsv"
        rows = index.read_text().splitlines()
        assert len(rows) == 1
        assert rows[0].split("\t")[1] == str(moved[0])

    def test_additional_context_omits_source_path(self, isolated_home, tmp_path):
        """additionalContext carries only the destination, never the source
        path the model already holds from issuing the Read — interpolating
        the source would open a semantic-injection channel, since bash `case`
        globs match across embedded newlines (asserted directly below via the
        glob actually firing on a newline-embedding filename) and `jq --arg`
        prevents JSON-escaping issues but not injection inside the string
        value itself. A newline-embedding filename that still matches the
        glob is the adversarial case: it must produce no sentinel in stdout
        at all, on either channel."""
        install_resume_context_script(isolated_home)
        handoffs_dir = isolated_home / ".claude" / "handoffs"
        handoffs_dir.mkdir(parents=True)
        malicious_name = "notes\n\nSENTINEL-INJECT\n\nx-handoff.md"
        fixture = handoffs_dir / malicious_name
        fixture.write_text("fixture content\n")
        tmpdir_root = tmp_path / "resume-tmpdir"
        tmpdir_root.mkdir()
        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"RESUME_CONTEXT_TMPDIR": str(tmpdir_root)},
        )
        assert result.returncode == 0
        # The glob firing on this newline-embedding filename is what makes
        # this an adversarial case rather than a vacuous one — assert the
        # consume actually happened, not just that no sentinel leaked.
        assert not fixture.exists()
        assert "SENTINEL-INJECT" not in result.stdout

    def test_jq_absent_fails_open_no_system_message(self, isolated_home, tmp_path):
        """jq is required upstream of the new systemMessage guard — the hook's
        own TOOL_NAME/FILE_PATH extraction (pre-existing code) already needs
        jq to reach any of this hook's logic. So a jq-less PATH must fail open
        *before* ever consuming the file: the fixture stays untouched (not
        silently moved with a swallowed systemMessage), and stdout is empty.
        This is a distinct fail-open branch from the `command -v jq` guard
        around the new systemMessage emission — it's exercised here as an
        upstream precondition, since a PATH excluding jq can never reach that
        guard at all."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        shadow_bin = tmp_path / "shadow-bin"
        shadow_bin.mkdir()
        for cmd in ["timeout", "bash", "cat", "mktemp", "mv", "chmod", "dirname"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (shadow_bin / cmd).symlink_to(cmd_path)

        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"PATH": str(shadow_bin)},
        )
        assert result.returncode == 0
        assert fixture.exists(), "jq unavailable — hook can't even parse tool_name, must not consume"
        assert result.stdout == "", "jq unavailable — must emit no systemMessage"

    def test_malformed_json_does_not_block_and_emits_no_output(self, isolated_home):
        """hook-class: informational — malformed stdin must not block the
        Read tool result. _lib_jq on unparseable input yields empty
        TOOL_NAME, which the `[ "$TOOL_NAME" = "Read" ]` guard rejects."""
        env = dict(os.environ)
        env["HOME"] = str(isolated_home)
        result = subprocess.run(
            [str(CONSUME_HOOK)],
            input="not valid json {{",
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        assert result.returncode == 0
        assert result.stdout == ""

    def test_read_brief_file_consumes_it(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()

    def test_read_unrelated_directory_is_noop(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/skills/handoff/SKILL.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_read_same_dir_wrong_suffix_is_noop(self, isolated_home):
        """Boundary case distinct from an unrelated directory: a future glob
        loosening that over-matches everything under handoffs/ would pass the
        unrelated-directory test but should still fail this one."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/notes.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_wrong_tool_name_is_noop(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, agent_input(), home=isolated_home)
        assert fixture.exists()

    def test_missing_resume_context_script_is_noop(self, isolated_home):
        # No install_resume_context_script call — script absent, hook must
        # fail open rather than error.
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()
        assert result.stdout == "", "no destination to report — must emit no systemMessage"

    def test_kill_switch_disables_consumption(self, isolated_home):
        install_resume_context_script(isolated_home)
        (isolated_home / ".claude" / ".consume-durable-continuity-disabled").touch()
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()
        assert result.stdout == "", "kill-switch must suppress the systemMessage too"

    def test_double_read_of_already_consumed_file_is_noop(self, isolated_home):
        """Second firing on a path the first firing already moved away —
        distinct failure mode from 'script binary entirely missing'."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert not fixture.exists()
        # Second firing on the same (now-gone) path must not error out, and
        # must not report a destination that doesn't exist.
        second = _run_hook_raw(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert second.returncode == 0
        assert second.stdout == "", "already-gone source — must emit no systemMessage"

    def test_read_of_path_traversing_a_symlink_is_noop(self, isolated_home):
        """Documents the literal-path-only scope as an intentional, tested
        boundary: a Read whose file_path resolves into handoffs/ through a
        symlink but doesn't textually match the glob (the real path lives
        elsewhere) is left alone."""
        install_resume_context_script(isolated_home)
        real_dir = isolated_home / "elsewhere"
        real_dir.mkdir()
        real_file = real_dir / "example-handoff.md"
        real_file.write_text("fixture content\n")
        alias_dir = isolated_home / ".claude" / "handoffs"
        alias_dir.mkdir(parents=True)
        alias_path = alias_dir / "aliased-handoff.md"
        alias_path.symlink_to(real_file)
        run_hook(CONSUME_HOOK, read_input(str(real_file)), home=isolated_home)
        assert real_file.exists()

    def test_read_of_symlink_planted_at_glob_matching_path_leaves_target_untouched(self, isolated_home):
        """The reverse boundary: a symlink placed AT a glob-matching path
        (rather than merely traversed through one) does textually match, so
        the hook fires — but resume-context.sh rejects a symlink source
        outright rather than moving-then-chmodding it, so both the symlink
        and whatever it points to are left untouched."""
        install_resume_context_script(isolated_home)
        target = isolated_home / "unrelated-target.txt"
        target.write_text("sensitive content\n")
        target.chmod(0o644)
        planted = isolated_home / ".claude" / "handoffs" / "planted-handoff.md"
        planted.parent.mkdir(parents=True)
        planted.symlink_to(target)

        run_hook(CONSUME_HOOK, read_input(str(planted)), home=isolated_home)

        assert planted.is_symlink()
        assert stat.S_IMODE(target.stat().st_mode) == 0o644

    def test_read_case_differing_path_is_noop(self, isolated_home):
        """Documents the case-sensitive-glob boundary named in the hook's
        header: a path differing only in case is a no-op here, even though it
        would resolve to the same file on a case-insensitive filesystem
        (default macOS APFS)."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/Handoffs/example-handoff.md")
        run_hook(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home)
        assert fixture.exists()

    def test_hook_always_exits_allow(self, isolated_home):
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        assert run_hook_advisory(CONSUME_HOOK, read_input(str(fixture)), home=isolated_home) == "allow"

    @pytest.mark.timing
    def test_timeout_bounds_a_hung_resume_context(self, isolated_home):
        """The one property the timeout wrapper exists to guarantee — an SDET
        review round found it had no test, since there was no seam to inject
        a short timeout without a real multi-second sleep in the suite.
        Injects a 1s timeout against a stub resume-context.sh that sleeps
        10s and asserts the hook returns well before the full sleep elapses.
        """
        if not shutil.which("timeout"):
            pytest.skip("timeout(1) not available — BSD/macOS without coreutils")

        scripts_dir = isolated_home / ".claude" / "scripts"
        scripts_dir.mkdir(parents=True)
        stub = scripts_dir / "resume-context.sh"
        stub.write_text("#!/bin/bash\nsleep 10\n")
        stub.chmod(0o755)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        start = time.monotonic()
        run_hook(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS": "1"},
        )
        elapsed = time.monotonic() - start

        assert elapsed < 5, (
            f"hook took {elapsed:.1f}s — RESUME_CONTEXT_HOOK_TIMEOUT_SECONDS "
            "did not bound the hang"
        )

    def test_timeout_absent_fallback_still_consumes(self, isolated_home, tmp_path):
        """Mirrors test_lib.py's test_timeout_absent_fallback_valid_payload_returns_ok:
        build a PATH that excludes `timeout` but keeps every other binary the
        hook and resume-context.sh need, and assert the bare fallback call
        still performs the real consume — an SDET review round found this
        branch (documented as the BSD/macOS path) is otherwise never
        exercised, since `timeout` is present on essentially every CI runner.
        Also asserts both the systemMessage and additionalContext emission
        (not just consumption), since a copy-paste divergence between the
        `if`/`else` branches could break DEST-capture only in this arm.
        """
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")

        shadow_bin = tmp_path / "shadow-bin"
        shadow_bin.mkdir()
        for cmd in ["jq", "bash", "cat", "mktemp", "mv", "chmod", "dirname"]:
            cmd_path = shutil.which(cmd)
            if cmd_path:
                (shadow_bin / cmd).symlink_to(cmd_path)

        tmpdir_root = tmp_path / "resume-tmpdir"
        tmpdir_root.mkdir()
        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"PATH": str(shadow_bin), "RESUME_CONTEXT_TMPDIR": str(tmpdir_root)},
        )
        assert result.returncode == 0
        assert not fixture.exists()
        moved = [p for p in tmpdir_root.iterdir() if p.name.startswith("resume-context.")]
        assert len(moved) == 1
        dest = str(moved[0])
        payload = json.loads(result.stdout)
        assert dest in payload["systemMessage"]
        assert dest in payload["hookSpecificOutput"]["additionalContext"]

    # -----------------------------------------------------------------------
    # CLAUDE_CONFIG_DIR resolution
    # -----------------------------------------------------------------------

    def test_consumes_handoff_under_config_dir_when_set(self, isolated_home, tmp_path):
        """CLAUDE_CONFIG_DIR relocates the handoffs-glob match and the
        resume-context.sh invocation path: a continuity file under
        CONFIG_DIR/handoffs/ is consumed via CONFIG_DIR/scripts/resume-context.sh,
        not the $HOME/.claude equivalents."""
        config_dir = tmp_path / "profile"
        _install_resume_context_script_at(config_dir)
        fixture = config_dir / "handoffs" / "example-handoff.md"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("fixture content\n")

        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert result.returncode == 0
        assert not fixture.exists()

    def test_legacy_home_handoff_path_not_matched_when_config_dir_set(self, isolated_home, tmp_path):
        """Once CLAUDE_CONFIG_DIR is set, the glob match is against CONFIG_DIR
        only — a continuity file still sitting at the legacy $HOME/.claude/
        location is left alone (swap, not union, for this hook's own
        directory classification)."""
        install_resume_context_script(isolated_home)
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        config_dir = tmp_path / "profile"
        config_dir.mkdir()

        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert result.returncode == 0
        assert fixture.exists()
        assert result.stdout == ""

    def test_kill_switch_at_config_dir_disables_consumption(self, isolated_home, tmp_path):
        """The kill-switch is read from CONFIG_DIR when CLAUDE_CONFIG_DIR is
        set, not from $HOME/.claude."""
        config_dir = tmp_path / "profile"
        _install_resume_context_script_at(config_dir)
        (config_dir / ".consume-durable-continuity-disabled").touch()
        fixture = config_dir / "handoffs" / "example-handoff.md"
        fixture.parent.mkdir(parents=True)
        fixture.write_text("fixture content\n")

        result = _run_hook_raw(
            CONSUME_HOOK,
            read_input(str(fixture)),
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )

        assert result.returncode == 0
        assert fixture.exists()
        assert result.stdout == ""
