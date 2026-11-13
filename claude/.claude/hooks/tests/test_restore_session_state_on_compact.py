"""Tests for restore-session-state-on-compact.sh.

The hook is a SessionStart hook (matcher: compact) that emits `additionalContext` holding the state block
`compaction-context.py state` builds from the transcript the payload names. It is advisory and fails open: every
failure path exits 0 with no output.

The block's content rules are tested in claude/.claude/scripts/tests/test_compaction_context_*.py. These tests cover the
hook's wiring: its self-filter, its failure paths, its cap, its encoding of the block, and its registration. The two parity
tests at the end tie two duplicated values in the script to the sources they copy.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    SCRIPTS_DIR,
    assert_cap_engaged,
    build_path_without,
    fake_bin_dir,
    fake_python3,
    run_additional_context_session_start_hook,
    scaled_shim_sleep,
    session_start_additional_context,
    session_start_matchers_registering,
    session_start_registered_timeouts,
    session_start_registrations,
    symlink_hooks_lib_chain,
    write_scaled_timeout_shim,
)

from .conftest import requires_jq
from .test_lib_path_char_allowlist import _run_sweep

RESTORE_STATE_HOOK = HOOKS_DIR / "restore-session-state-on-compact.sh"
RESTORE_BOUNDARY_HOOK = HOOKS_DIR / "restore-authorization-boundary-on-compact.sh"
COMPACTION_CONTEXT_SCRIPT = SCRIPTS_DIR / "compaction-context.py"

# The script's time cap and _lib_capped_for's SIGKILL grace, which the settings timeout has to exceed.
SCRIPT_CAP_SECONDS = 6
KILL_GRACE_SECONDS = 2
# A production-scale stall far past the cap, so a hook that waited it out is told apart from one the cap killed.
STALL_PAST_CAP_SECONDS = 60

SKILL_DIR = "/work/repo/.claude/skills/scratch"
SKILL_MD_PATH = f"{SKILL_DIR}/SKILL.md"
NEWEST_PROMPT = "the newest typed prompt"


def _skill_load_record() -> dict:
    body = f"Base directory for this skill: {SKILL_DIR}\n\n# Scratch skill\n\n1. First step."
    return {
        "type": "user",
        "isMeta": True,
        "entrypoint": "cli",
        "promptId": "prompt-1",
        "message": {"role": "user", "content": [{"type": "text", "text": body}]},
    }


def _typed_prompt(text: str) -> dict:
    return {
        "type": "user",
        "origin": {"kind": "human"},
        "entrypoint": "cli",
        "promptSource": "typed",
        "message": {"role": "user", "content": text},
    }


def _headless_prompt(text: str) -> dict:
    """A `claude -p` prompt: no `origin`, with the three markers the script requires in its place."""
    record = {key: value for key, value in _typed_prompt(text).items() if key != "origin"}
    return {**record, "entrypoint": "sdk-cli", "promptSource": "sdk", "turnOrigin": "sdk"}


def _write_transcript(directory: Path, records: list[dict]) -> Path:
    path = directory / "session.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def _payload(transcript: Path, **overrides) -> dict:
    return {"source": "compact", "transcript_path": str(transcript), **overrides}


def _run_hook(hook: Path, payload: dict | None, home: Path, **kwargs) -> subprocess.CompletedProcess:
    return run_additional_context_session_start_hook(hook, payload, home, **kwargs)


def _assert_silent(result: subprocess.CompletedProcess) -> None:
    assert result.returncode == 0
    assert result.stdout == b""


def _assert_emits_state_block(result: subprocess.CompletedProcess, expected_text: str = SKILL_MD_PATH) -> str:
    assert result.returncode == 0
    context = session_start_additional_context(result)
    assert expected_text in context
    return context


@pytest.fixture
def transcript(tmp_path) -> Path:
    return _write_transcript(tmp_path, [_skill_load_record(), _typed_prompt(NEWEST_PROMPT)])


def _relocated_hook(tmp_path: Path, *, with_lib: bool = True, with_script: bool = True) -> Path:
    """A hook symlinked into a fresh `<root>/hooks`, with an optional `<root>/scripts` beside it where the hook looks."""
    hooks_dir = tmp_path / "relocated" / "hooks"
    hooks_dir.mkdir(parents=True)
    (hooks_dir / RESTORE_STATE_HOOK.name).symlink_to(RESTORE_STATE_HOOK)
    if with_lib:
        symlink_hooks_lib_chain(hooks_dir)
    if with_script:
        (hooks_dir.parent / "scripts").symlink_to(SCRIPTS_DIR)
    return hooks_dir / RESTORE_STATE_HOOK.name


requires_timeout = pytest.mark.skipif(
    shutil.which("timeout") is None and shutil.which("gtimeout") is None,
    reason="neither timeout(1) nor gtimeout(1) available -- BSD/macOS without coreutils",
)


@requires_jq
class TestSourceFilter:
    def test_compact_source_emits_the_state_block_as_session_start_additional_context(self, isolated_home, transcript):
        result = _run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home)

        context = _assert_emits_state_block(result)
        assert NEWEST_PROMPT in context
        assert json.loads(result.stdout)["hookSpecificOutput"]["hookEventName"] == "SessionStart"

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("startup", id="startup_source"),
            pytest.param("resume", id="resume_source"),
            pytest.param("clear", id="clear_source"),
            pytest.param(None, id="missing_source"),
            pytest.param(5, id="non_string_source"),
        ],
    )
    def test_source_other_than_compact_gives_no_output(self, isolated_home, transcript, source):
        payload = _payload(transcript)
        payload.pop("source")
        if source is not None:
            payload["source"] = source

        _assert_silent(_run_hook(RESTORE_STATE_HOOK, payload, isolated_home))
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    def test_empty_stdin_gives_no_output(self, isolated_home, transcript):
        _assert_silent(_run_hook(RESTORE_STATE_HOOK, None, isolated_home, raw_stdin=b""))
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))


@requires_jq
class TestTranscriptPath:
    @pytest.mark.parametrize(
        "make_payload",
        [
            pytest.param(lambda tmp_path, transcript: {"source": "compact"}, id="missing_transcript_path"),
            pytest.param(
                lambda tmp_path, transcript: {"source": "compact", "transcript_path": 7}, id="non_string_transcript_path"
            ),
            pytest.param(
                lambda tmp_path, transcript: _payload(tmp_path / "absent.jsonl"), id="transcript_path_that_is_gone"
            ),
            pytest.param(lambda tmp_path, transcript: _payload(tmp_path), id="transcript_path_that_is_a_directory"),
        ],
    )
    def test_unusable_transcript_path_gives_no_output(self, isolated_home, tmp_path, transcript, make_payload):
        _assert_silent(_run_hook(RESTORE_STATE_HOOK, make_payload(tmp_path, transcript), isolated_home))
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    @pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
    def test_unreadable_transcript_gives_no_output(self, isolated_home, transcript):
        payload = _payload(transcript)
        transcript.chmod(0)
        try:
            _assert_silent(_run_hook(RESTORE_STATE_HOOK, payload, isolated_home))
        finally:
            transcript.chmod(0o644)
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, payload, isolated_home))

    @pytest.mark.parametrize(
        "file_name",
        [
            pytest.param("with space.jsonl", id="spaces"),
            pytest.param("line\nbreak.jsonl", id="embedded_newline"),
            pytest.param("meta;$(touch PWNED)`touch PWNED`'\"&|<>*?.jsonl", id="shell_metacharacters"),
        ],
    )
    def test_path_hostile_to_a_shell_reaches_the_script_as_one_argument(self, isolated_home, tmp_path, transcript, file_name):
        directory = tmp_path / "hostile"
        directory.mkdir()
        hostile_transcript = directory / file_name
        hostile_transcript.write_bytes(transcript.read_bytes())

        result = _run_hook(RESTORE_STATE_HOOK, _payload(hostile_transcript), isolated_home, process_cwd=directory)

        _assert_emits_state_block(result)
        assert not (directory / "PWNED").exists()

    def test_relative_path_with_a_leading_dash_gives_no_output_unless_written_with_a_dot_slash(
        self, isolated_home, tmp_path, transcript
    ):
        directory = tmp_path / "dash"
        directory.mkdir()
        (directory / "-session.jsonl").write_bytes(transcript.read_bytes())

        silent = _run_hook(
            RESTORE_STATE_HOOK, {"source": "compact", "transcript_path": "-session.jsonl"}, isolated_home, process_cwd=directory
        )
        control = _run_hook(
            RESTORE_STATE_HOOK, {"source": "compact", "transcript_path": "./-session.jsonl"}, isolated_home, process_cwd=directory
        )

        _assert_silent(silent)
        _assert_emits_state_block(control)

    def test_symlink_to_a_transcript_is_read_through(self, isolated_home, tmp_path, transcript):
        link = tmp_path / "linked-session.jsonl"
        link.symlink_to(transcript)

        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(link), isolated_home))


@requires_jq
class TestFailOpen:
    def test_missing_python3_gives_no_output(self, isolated_home, tmp_path, transcript):
        farm_dir = tmp_path / "path_without_python3"
        farm_dir.mkdir()
        path_without_python3 = build_path_without("python3", farm_dir)

        silent = _run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_without_python3})

        _assert_silent(silent)
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    def test_missing_jq_gives_no_output(self, isolated_home, tmp_path, transcript):
        farm_dir = tmp_path / "path_without_jq"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)

        silent = _run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_without_jq})

        _assert_silent(silent)
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    def test_unsourceable_lib_gives_no_output(self, isolated_home, tmp_path, transcript):
        hook_without_lib = _relocated_hook(tmp_path, with_lib=False)

        _assert_silent(_run_hook(hook_without_lib, _payload(transcript), isolated_home))
        # Control: the same layout with the lib chain restored.
        symlink_hooks_lib_chain(hook_without_lib.parent)
        _assert_emits_state_block(_run_hook(hook_without_lib, _payload(transcript), isolated_home))

    def test_absent_script_gives_no_output(self, isolated_home, tmp_path, transcript):
        hook_without_script = _relocated_hook(tmp_path, with_script=False)

        _assert_silent(_run_hook(hook_without_script, _payload(transcript), isolated_home))
        # Control: the same layout with the script directory placed beside the hooks directory.
        (hook_without_script.parent.parent / "scripts").symlink_to(SCRIPTS_DIR)
        _assert_emits_state_block(_run_hook(hook_without_script, _payload(transcript), isolated_home))

    def test_neither_timeout_nor_gtimeout_on_path_still_emits_the_block_uncapped(self, isolated_home, tmp_path, transcript):
        farm_dir = tmp_path / "path_without_timeout_or_gtimeout"
        farm_dir.mkdir()
        path_without_either = build_path_without("timeout", farm_dir)
        (farm_dir / "gtimeout").unlink(missing_ok=True)
        assert shutil.which("timeout", path=path_without_either) is None
        assert shutil.which("gtimeout", path=path_without_either) is None

        result = _run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_without_either})

        _assert_emits_state_block(result)

    def test_script_that_prints_then_exits_nonzero_gives_no_output(self, isolated_home, tmp_path, transcript):
        fake_bin = fake_bin_dir(tmp_path, "prints-then-fails")
        path_with_failing_script = fake_python3(fake_bin, "#!/bin/bash\necho PARTIAL-BLOCK\nexit 1\n")

        silent = _run_hook(
            RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_with_failing_script}
        )

        _assert_silent(silent)
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    def test_script_with_empty_output_gives_no_output(self, isolated_home, tmp_path, transcript):
        fake_bin = fake_bin_dir(tmp_path, "prints-nothing")
        path_with_silent_script = fake_python3(fake_bin, "#!/bin/bash\nexit 0\n")

        silent = _run_hook(
            RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_with_silent_script}
        )

        _assert_silent(silent)
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))

    @pytest.mark.timing
    @requires_timeout
    def test_script_stalled_past_the_cap_gives_no_output_within_a_bounded_wall_time(self, isolated_home, tmp_path, transcript):
        fake_bin = fake_bin_dir(tmp_path, "stalls")
        write_scaled_timeout_shim(fake_bin)
        stall_seconds = scaled_shim_sleep(STALL_PAST_CAP_SECONDS)
        # `exec` leaves sleep as timeout's direct child, so the cap's SIGTERM ends the process holding the output pipe.
        path_with_stalled_script = fake_python3(fake_bin, f"#!/bin/bash\nexec sleep {stall_seconds}\n")
        started = time.monotonic()

        with assert_cap_engaged(fake_bin, production_cap=SCRIPT_CAP_SECONDS):
            silent = _run_hook(
                RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_with_stalled_script}
            )

        _assert_silent(silent)
        assert time.monotonic() - started < stall_seconds / 2
        _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript), isolated_home))


@requires_jq
class TestHappyPath:
    def test_plan_line_appears_only_when_the_plan_file_exists(self, isolated_home, tmp_path):
        checkout = tmp_path / "checkout"
        (checkout / ".git").mkdir(parents=True)
        plan = checkout / ".claude" / "plans" / "scratch.md"
        on_branch = {"gitBranch": "feature/scratch", "cwd": str(checkout)}
        records = [{**_skill_load_record(), **on_branch}, _typed_prompt(NEWEST_PROMPT)]
        transcript_path = _write_transcript(tmp_path, records)

        without_plan = _run_hook(RESTORE_STATE_HOOK, _payload(transcript_path), isolated_home)
        plan.parent.mkdir(parents=True)
        plan.write_text("# Plan\n")
        with_plan = _run_hook(RESTORE_STATE_HOOK, _payload(transcript_path), isolated_home)

        assert str(plan) not in _assert_emits_state_block(without_plan)
        assert f"`{plan}`" in _assert_emits_state_block(with_plan)

    def test_additional_context_equals_the_script_stdout_for_text_the_json_encoding_could_mangle(
        self, isolated_home, tmp_path
    ):
        tricky_prompt = "café 日本 😀 \"double\" 'single' back\\slash `tick` $HOME\n~~~\nafter the tilde run"
        transcript_path = _write_transcript(tmp_path, [_skill_load_record(), _typed_prompt(tricky_prompt)])
        script_result = subprocess.run(
            ["python3", str(COMPACTION_CONTEXT_SCRIPT), "state", "--transcript", str(transcript_path)],
            capture_output=True,
            check=True,
        )

        result = _run_hook(RESTORE_STATE_HOOK, _payload(transcript_path), isolated_home)

        context = _assert_emits_state_block(result, tricky_prompt)
        assert context == script_result.stdout.decode("utf-8").rstrip("\n")

    def test_headless_prompt_keeps_its_label_directly_above_its_fence(self, isolated_home, tmp_path):
        headless_text = "the headless prompt"
        records = [_skill_load_record(), _typed_prompt(NEWEST_PROMPT), _headless_prompt(headless_text)]
        transcript_path = _write_transcript(tmp_path, records)
        label = _load_compaction_context_module().HEADLESS_LABEL

        context = _assert_emits_state_block(_run_hook(RESTORE_STATE_HOOK, _payload(transcript_path), isolated_home))

        lines = context.split("\n")
        headless_fence_index = lines.index(headless_text) - 1
        human_fence_index = lines.index(NEWEST_PROMPT) - 1
        assert lines[headless_fence_index].startswith("~~~")
        assert lines[headless_fence_index - 1] == label
        assert lines[human_fence_index].startswith("~~~")
        assert lines[human_fence_index - 1] != label

    def test_block_reaches_jq_on_stdin_and_never_in_its_argv(self, isolated_home, tmp_path):
        sentinel = "ARGV-SENTINEL-7f3a"
        transcript_path = _write_transcript(tmp_path, [_typed_prompt(f"prompt holding {sentinel}")])
        fake_bin = fake_bin_dir(tmp_path, "logs-jq-argv")
        argv_log = tmp_path / "jq-argv.log"
        jq_shim = fake_bin / "jq"
        jq_shim.write_text(f'#!/bin/bash\nprintf \'%s\\n\' "$*" >> "{argv_log}"\nexec "{shutil.which("jq")}" "$@"\n')
        jq_shim.chmod(0o755)

        result = _run_hook(
            RESTORE_STATE_HOOK,
            _payload(transcript_path),
            isolated_home,
            extra_env={"PATH": f"{fake_bin}:{os.environ['PATH']}"},
        )

        _assert_emits_state_block(result, sentinel)
        logged_argv = argv_log.read_text()
        assert logged_argv != ""
        assert sentinel not in logged_argv

    def test_script_stderr_never_reaches_the_hook_stderr(self, isolated_home, tmp_path, transcript):
        fake_bin = fake_bin_dir(tmp_path, "writes-stderr")
        script_body = "#!/bin/bash\necho SCRIPT-STDERR-SENTINEL >&2\necho SCRIPT-BLOCK\n"
        path_with_noisy_script = fake_python3(fake_bin, script_body)

        result = _run_hook(
            RESTORE_STATE_HOOK, _payload(transcript), isolated_home, extra_env={"PATH": path_with_noisy_script}
        )

        _assert_emits_state_block(result, "SCRIPT-BLOCK")
        assert b"SCRIPT-STDERR-SENTINEL" not in result.stderr


class TestRegistration:
    def _session_start_groups(self) -> list[dict]:
        return json.loads((CLAUDE_DIR / "settings.json").read_text())["hooks"]["SessionStart"]

    def test_matcher_is_compact_alone(self):
        assert session_start_matchers_registering(RESTORE_STATE_HOOK.name) == ["compact"]

    def test_hook_sits_alone_in_its_own_group(self):
        groups = [
            group
            for group in self._session_start_groups()
            if any(hook.get("command", "").endswith(f"/{RESTORE_STATE_HOOK.name}") for hook in group["hooks"])
        ]

        assert len(groups) == 1
        assert len(groups[0]["hooks"]) == 1

    def test_timeout_exceeds_the_script_cap_plus_the_kill_grace(self):
        timeouts = session_start_registered_timeouts(RESTORE_STATE_HOOK.name)

        assert len(timeouts) == 1
        assert timeouts[0] > SCRIPT_CAP_SECONDS + KILL_GRACE_SECONDS

    def test_command_uses_the_same_form_as_the_sibling_compact_hook(self):
        registrations = session_start_registrations()
        sibling_commands = [
            hook["command"] for _, hook in registrations if hook.get("command", "").endswith(f"/{RESTORE_BOUNDARY_HOOK.name}")
        ]
        entries = [hook for _, hook in registrations if hook.get("command", "").endswith(f"/{RESTORE_STATE_HOOK.name}")]

        assert len(sibling_commands) == 1
        assert [entry["command"] for entry in entries] == [
            sibling_commands[0].replace(RESTORE_BOUNDARY_HOOK.name, RESTORE_STATE_HOOK.name)
        ]
        assert "onFailure" not in entries[0]


def _load_compaction_context_module():
    """The script as a module; its sibling-package imports need the scripts directory on sys.path while it executes."""
    spec = importlib.util.spec_from_file_location("compaction_context_under_parity_test", COMPACTION_CONTEXT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS_DIR))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.remove(str(SCRIPTS_DIR))
    return module


class TestParity:
    def test_path_class_accepts_the_same_bytes_as_the_bash_allowlist(self):
        compaction_context = _load_compaction_context_module()
        values = [bytes([byte]) for byte in range(1, 256)]
        verdicts = _run_sweep(values)
        assert len(verdicts) == len(values)

        bash_accepted = {value[0] for value, verdict in zip(values, verdicts, strict=True) if verdict == ord("1")}
        python_accepted = {value[0] for value in values if compaction_context._PATH_RE.fullmatch(chr(value[0]))}

        assert python_accepted == bash_accepted
        assert bash_accepted

    def test_value_of_allowed_bytes_ending_in_a_newline_is_rejected_by_both(self):
        compaction_context = _load_compaction_context_module()

        assert _run_sweep([b"ab\n", b"ab"]) == b"01"
        assert compaction_context._PATH_RE.fullmatch("ab\n") is None
        assert compaction_context._PATH_RE.fullmatch("ab") is not None

    @requires_jq
    def test_irreversible_action_set_appears_verbatim_in_the_boundary_hooks_runtime_output(self, isolated_home):
        compaction_context = _load_compaction_context_module()

        result = _run_hook(RESTORE_BOUNDARY_HOOK, {"source": "compact"}, isolated_home)

        assert result.returncode == 0
        assert compaction_context.IRREVERSIBLE_ACTION_SET in session_start_additional_context(result)
