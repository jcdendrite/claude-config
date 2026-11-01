"""Tests for record-session-config-keys.sh.

SessionStart hook that appends one row per event, holding the resolved value of every
config-keys.psv key, to <config-dir>/.session-config-keys-log.jsonl, and prints nothing when it
does. When it cannot append a row it prints one fixed notice line. Every path exits 0 with empty
stderr.

Expected key values are built from the schema text a case runs against and the state the case
writes, never through `_config_value` or any other resolver.
"""
from __future__ import annotations

import functools
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from helpers import (
    CLAUDE_DIR,
    HOOKS_DIR,
    REPO_ROOT,
    _build_subprocess_env,
    build_isolated_hook,
    build_path_without,
    git_isolation_env,
    installed_locale_where,
)

from .conftest import requires_jq
from .test_bash_floor import SUB_FLOOR_BASH, requires_sub_floor_bash

HOOK = HOOKS_DIR / "record-session-config-keys.sh"
REGISTERED_COMMAND = "~/.claude/hooks/record-session-config-keys.sh"
SETTINGS_JSON = CLAUDE_DIR / "settings.json"
SCHEMA_PATH = HOOKS_DIR / "config-keys.psv"
READER_DOC = REPO_ROOT / "docs/session-config-keys-log.md"

LOG_FILENAME = ".session-config-keys-log.jsonl"
SESSION_ID = "0b6b0f5e-3c58-4a3d-9f2e-7d4a1c8e5b21"
# A POSIX zone string with a non-whole-hour offset: it needs no tz database, and a hook that
# formatted local time instead of UTC would fail the logged_at window.
TZ_NON_WHOLE_HOUR = "<+0530>-05:30"
# Same bound run_reconciler (helpers.py) gives its subprocess, so a hook that blocks fails its case.
HOOK_TIMEOUT_SECONDS = 30
# A row under this size goes out in one write(2) on a local filesystem, so concurrent appends cannot interleave inside a line.
ROW_SIZE_BOUND_BYTES = 2048
# Enough hook processes to overlap on the create and the append without making the case slow.
CONCURRENT_SESSION_COUNT = 16

NOTICE_SCREEN_TEXT = (
    "record-session-config-keys.sh wrote no config-key row for this session event. "
    "See docs/session-config-keys-log.md in the claude-config repository."
)
NOTICE_AGENT_TEXT = (
    "record-session-config-keys.sh wrote no config-key row for this session event. "
    "In your next reply, tell the engineer so and point them to docs/session-config-keys-log.md "
    "in the claude-config repository. "
    "Do nothing else about it unless the engineer asks: read no files for it, file no issue, "
    "write no row and leave the hook unchanged."
)
NOTICE_LINE = (
    '{"systemMessage":"record-session-config-keys.sh wrote no config-key row for this session event. '
    'See docs/session-config-keys-log.md in the claude-config repository.",'
    '"hookSpecificOutput":{"hookEventName":"SessionStart",'
    '"additionalContext":"record-session-config-keys.sh wrote no config-key row for this session event. '
    "In your next reply, tell the engineer so and point them to docs/session-config-keys-log.md "
    "in the claude-config repository. "
    "Do nothing else about it unless the engineer asks: read no files for it, file no issue, "
    'write no row and leave the hook unchanged."}}\n'
)

SCHEMA_COLUMNS = (
    "key",
    "type",
    "default",
    "resolution",
    "legacy_probe",
    "import_locations",
    "legacy_filename",
    "legacy_polarity",
    "human_name",
    "docs_anchor",
    "prompt_description",
)
CITED_DOC_PATH = re.compile(r"docs/[\w.-]+\.md")
VALUE_TOKEN = re.compile(r"[a-z0-9_-]+")
UNREADABLE = "unreadable"


# --------------------------------------------------------------------------- #
# Schema oracle: parses schema text the way config-keys.psv's own header      #
# describes, independent of every resolver.                                   #
# --------------------------------------------------------------------------- #


def schema_rows(schema_text: str) -> list[dict[str, str]]:
    rows = []
    for line in schema_text.splitlines():
        if not line or line.startswith("#"):
            continue
        fields = line.split("|")
        assert len(fields) == len(SCHEMA_COLUMNS), line
        rows.append(dict(zip(SCHEMA_COLUMNS, fields, strict=True)))
    return rows


def assert_polarity_agrees_with_default(rows: list[dict[str, str]]) -> None:
    """A schema change that breaks the polarity/default agreement must fail here, not skew the oracle."""
    for row in rows:
        if row["legacy_polarity"] in ("presence-enables", "content-matches"):
            assert row["default"] == "false", row["key"]
        elif row["legacy_polarity"] == "presence-disables":
            assert row["default"] == "true", row["key"]


def no_state_values(schema_text: str) -> dict[str, str]:
    """Each key's value with no state file and no legacy file: the default column, first row per key."""
    rows = schema_rows(schema_text)
    assert_polarity_agrees_with_default(rows)
    values: dict[str, str] = {}
    for row in rows:
        values.setdefault(row["key"], row["default"])
    return values


def first_row(schema_text: str, **column_equals: str) -> dict[str, str]:
    for row in schema_rows(schema_text):
        if all(row[column] == wanted for column, wanted in column_equals.items()):
            return row
    pytest.fail(f"schema has no row with {column_equals}")


SHIPPED_SCHEMA_TEXT = SCHEMA_PATH.read_text()


def shipped_keys() -> list[str]:
    """Called inside the cases that need it, so a schema change that breaks the oracle's asserts fails those cases by name."""
    return list(no_state_values(SHIPPED_SCHEMA_TEXT))


def one_row_schema_text() -> str:
    """The shipped header and only the first key row."""
    kept = []
    for line in SHIPPED_SCHEMA_TEXT.splitlines():
        kept.append(line)
        if line and not line.startswith("#"):
            break
    return "\n".join(kept) + "\n"


ONE_ROW_SCHEMA_TEXT = one_row_schema_text()


def one_row_expected_keys() -> dict[str, str]:
    values = no_state_values(ONE_ROW_SCHEMA_TEXT)
    assert len(values) == 1
    return values


def schema_with_row_edit(schema_text: str, key: str, column: str, new_value: str) -> str:
    """One schema row's column replaced, every other line left as it is."""
    column_index = SCHEMA_COLUMNS.index(column)
    lines = []
    for line in schema_text.splitlines():
        if line.startswith(f"{key}|"):
            fields = line.split("|")
            fields[column_index] = new_value
            line = "|".join(fields)
        lines.append(line)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Runner and stubs                                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Sandbox:
    root: Path
    home: Path
    config_dir: Path
    workdir: Path

    @property
    def log_path(self) -> Path:
        return self.config_dir / LOG_FILENAME


def make_sandbox(tmp_path: Path, *, config_dir_name: str = "config", create_config_dir: bool = True) -> Sandbox:
    home = tmp_path / "home"
    workdir = tmp_path / "work"
    config_dir = tmp_path / config_dir_name
    home.mkdir()
    workdir.mkdir()
    if create_config_dir:
        config_dir.mkdir()
    return Sandbox(root=tmp_path, home=home, config_dir=config_dir, workdir=workdir)


def isolated_hook(tmp_path: Path, schema_text: str | None, *, name: str = "hook-dir") -> Path:
    """The hook beside its libraries and a schema holding `schema_text`, in its own directory."""
    hook_dir = tmp_path / name
    hook_dir.mkdir()
    return build_isolated_hook(hook_dir, HOOK, schema_text=schema_text)


def payload_json(*, source: str | None = "startup", **overrides: object) -> str:
    payload: dict[str, object] = {"hook_event_name": "SessionStart", "session_id": SESSION_ID}
    if source is not None:
        payload["source"] = source
    payload.update(overrides)
    return json.dumps(payload)


@dataclass(frozen=True)
class HookRun:
    result: subprocess.CompletedProcess
    started_at: datetime
    finished_at: datetime

    @property
    def returncode(self) -> int:
        return self.result.returncode

    @property
    def stdout(self) -> str:
        return self.result.stdout

    @property
    def stderr(self) -> str:
        return self.result.stderr


def hook_env(
    sandbox: Sandbox,
    *,
    extra_env: dict[str, str] | None = None,
    unset: tuple[str, ...] = (),
    path: str | None = None,
    bash_env_file: Path | None = None,
) -> dict[str, str]:
    """HOME, CLAUDE_CONFIG_DIR and TZ always explicit. BASH_ENV and ENV are dropped first, so a
    shell startup file that prints cannot reach the exact output assertions, and a case's own
    BASH_ENV is applied after the drop because _build_subprocess_env merges before it."""
    env = _build_subprocess_env(
        sandbox.home,
        {"CLAUDE_CONFIG_DIR": str(sandbox.config_dir), "TZ": TZ_NON_WHOLE_HOUR, **(extra_env or {})},
    )
    assert env is not None
    for name in ("BASH_ENV", "ENV", *unset):
        env.pop(name, None)
    if path is not None:
        env["PATH"] = path
    if bash_env_file is not None:
        env["BASH_ENV"] = str(bash_env_file)
    return env


def run_hook(
    sandbox: Sandbox,
    stdin_text: str | None = None,
    *,
    hook: Path = HOOK,
    umask: int | None = None,
    command_prefix: tuple[str, ...] = (),
    **env_options: Any,
) -> HookRun:
    env = hook_env(sandbox, **env_options)
    started_at = datetime.now(UTC).replace(microsecond=0)
    result = subprocess.run(
        [*command_prefix, str(hook)],
        input=payload_json() if stdin_text is None else stdin_text,
        capture_output=True,
        text=True,
        cwd=sandbox.workdir,
        env=env,
        timeout=HOOK_TIMEOUT_SECONDS,
        umask=-1 if umask is None else umask,
        check=False,
    )
    return HookRun(result=result, started_at=started_at, finished_at=datetime.now(UTC))


def run_bare(sandbox: Sandbox, hooks_dir: Path, snippet: str, **env_options: Any) -> subprocess.CompletedProcess:
    """A control: sources the `_lib.sh` in `hooks_dir`, then runs `snippet`, in the hook's own environment."""
    return subprocess.run(
        ["bash", "-c", f'. "$1/_lib.sh" && {snippet}', "_", str(hooks_dir)],
        capture_output=True,
        text=True,
        cwd=sandbox.workdir,
        env=hook_env(sandbox, **env_options),
        timeout=HOOK_TIMEOUT_SECONDS,
        check=False,
    )


def path_in_front_of_real_path(bin_dir: Path) -> str:
    return f"{bin_dir}{os.pathsep}{os.environ['PATH']}"


@dataclass(frozen=True)
class Stubs:
    bin_dir: Path
    jq_calls: Path
    chmod_calls: Path

    @property
    def path(self) -> str:
        return path_in_front_of_real_path(self.bin_dir)

    def jq_call_kinds(self) -> list[str]:
        return self.jq_calls.read_text().splitlines() if self.jq_calls.exists() else []

    def encode_call_count(self) -> int:
        return self.jq_call_kinds().count("encode")

    def chmod_call_lines(self) -> list[str]:
        return self.chmod_calls.read_text().splitlines() if self.chmod_calls.exists() else []


JQ_ENCODE_FAILURE_BODIES = {
    "passthrough": "",
    # Prints a partial object and a stderr line, then fails.
    "partial_output_then_exit_1": "printf '{\"session_id\":'\n  echo 'spy: forced encode failure' >&2\n  exit 1",
    # Prints nothing and exits 0, which only the hook's `[ -n "$row" ]` guard stops.
    "no_output_exit_0": "exit 0",
}


def install_stubs(bin_dir: Path, *, jq_encode: str = "passthrough", chmod: str | None = None) -> Stubs:
    """A jq spy and, when `chmod` is given, a chmod stub, both first on PATH.

    The spy logs each call and runs the real jq, except that the row encode call, recognized by
    the argument `key_names` after `--arg`, follows `jq_encode`. That couples the spy to the
    hook's binding name, so renaming the binding fails the cases that look for the encode call.
    The chmod stub logs its arguments and exits 1 (`"fail"`) or 0 without changing a mode (`"noop"`).
    """
    real_jq = shutil.which("jq")
    assert real_jq is not None
    bash = shutil.which("bash")
    assert bash is not None
    bin_dir.mkdir()
    stubs = Stubs(bin_dir=bin_dir, jq_calls=bin_dir / "jq-calls.log", chmod_calls=bin_dir / "chmod-calls.log")
    spy = bin_dir / "jq"
    spy.write_text(
        f"#!{bash}\n"
        "encode=0\n"
        'previous=""\n'
        'for argument in "$@"; do\n'
        '  if [ "$previous" = --arg ] && [ "$argument" = key_names ]; then encode=1; fi\n'
        '  previous="$argument"\n'
        "done\n"
        'if [ "$encode" = 1 ]; then\n'
        f'  echo encode >> "{stubs.jq_calls}"\n'
        f"  {JQ_ENCODE_FAILURE_BODIES[jq_encode]}\n"
        "else\n"
        f'  echo other >> "{stubs.jq_calls}"\n'
        "fi\n"
        f'exec "{real_jq}" "$@"\n'
    )
    spy.chmod(0o755)
    if chmod is not None:
        exit_status = {"fail": 1, "noop": 0}[chmod]
        chmod_stub = bin_dir / "chmod"
        chmod_stub.write_text(f"#!{bash}\nprintf '%s\\n' \"$*\" >> \"{stubs.chmod_calls}\"\nexit {exit_status}\n")
        chmod_stub.chmod(0o755)
    return stubs


# --------------------------------------------------------------------------- #
# Assertions                                                                  #
# --------------------------------------------------------------------------- #


def assert_silent_success(run: HookRun) -> None:
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    assert run.stdout == ""


def assert_notice_only(run: HookRun) -> None:
    assert run.returncode == 0, run.stderr
    assert run.stderr == ""
    assert run.stdout == NOTICE_LINE


def assert_no_log_anywhere(tmp_path: Path) -> None:
    assert list(tmp_path.rglob(LOG_FILENAME)) == []


def non_blank_lines(text: str) -> list[str]:
    return [line for line in text.split("\n") if line.strip()]


def assert_row_appended(
    sandbox: Sandbox,
    run: HookRun,
    earlier_bytes: bytes,
    *,
    expected_source: str,
    expected_keys: dict[str, str],
    expected_non_blank_lines: int,
) -> dict:
    """The run left the earlier bytes alone and appended exactly: newline, one row line, newline."""
    assert_silent_success(run)
    after = sandbox.log_path.read_bytes()
    assert after.startswith(earlier_bytes)
    appended = after[len(earlier_bytes) :]
    assert appended[:1] == b"\n"
    assert appended[-1:] == b"\n"
    row_line = appended[1:-1]
    assert row_line
    assert b"\n" not in row_line

    row = json.loads(row_line)
    assert set(row) == {"session_id", "source", "logged_at", "keys"}
    assert row["session_id"] == SESSION_ID
    assert row["source"] == expected_source
    assert row["logged_at"].endswith("Z")
    logged_at = datetime.fromisoformat(row["logged_at"].replace("Z", "+00:00"))
    # `todateiso8601` drops the fraction, so the lower bound is the call's start truncated to the second.
    assert run.started_at <= logged_at <= run.finished_at
    assert row["keys"] == expected_keys
    assert all(VALUE_TOKEN.fullmatch(value) for value in row["keys"].values())
    assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600
    assert len(non_blank_lines(after.decode())) == expected_non_blank_lines
    return row


def assert_log_untouched(log_path: Path, *, expected_bytes: bytes, expected_mode: int) -> None:
    assert log_path.read_bytes() == expected_bytes
    assert stat.S_IMODE(log_path.stat().st_mode) == expected_mode


def assert_seam_intercepted(intercept_log: Path, *, guard: str, calls: int = 1) -> None:
    """A seam that never fired means the hook no longer makes the call it stands in for, so the case fails naming `guard`."""
    assert intercept_log.exists(), f"the seam never matched {guard}: the hook dropped it or changed its call form"
    intercepted = intercept_log.read_text().splitlines()
    assert intercepted == ["intercepted"] * calls, f"expected {calls} interception(s) of {guard}, saw {len(intercepted)}"


# --------------------------------------------------------------------------- #
# Case 1 and the notice constant                                              #
# --------------------------------------------------------------------------- #


class TestRegistration:
    def test_one_session_start_group_registers_the_hook_with_no_matcher_if_or_timeout(self):
        hooks_by_event = json.loads(SETTINGS_JSON.read_text())["hooks"]

        events_registering_hook = [
            event
            for event, groups in hooks_by_event.items()
            if any(entry.get("command") == REGISTERED_COMMAND for group in groups for entry in group["hooks"])
        ]
        matching_groups = [
            group
            for group in hooks_by_event["SessionStart"]
            if any(entry.get("command") == REGISTERED_COMMAND for entry in group["hooks"])
        ]

        assert events_registering_hook == ["SessionStart"]
        [group] = matching_groups
        assert "matcher" not in group
        [hook_entry] = group["hooks"]
        assert hook_entry["type"] == "command"
        assert "if" not in hook_entry
        assert "timeout" not in hook_entry


class TestNoticeConstant:
    def test_notice_line_is_the_two_channel_envelope_carrying_the_two_texts(self):
        assert NOTICE_LINE.endswith("\n")

        envelope = json.loads(NOTICE_LINE)

        assert set(envelope) == {"systemMessage", "hookSpecificOutput"}
        assert set(envelope["hookSpecificOutput"]) == {"hookEventName", "additionalContext"}
        assert envelope["hookSpecificOutput"]["hookEventName"] == "SessionStart"
        assert envelope["systemMessage"] == NOTICE_SCREEN_TEXT
        assert envelope["hookSpecificOutput"]["additionalContext"] == NOTICE_AGENT_TEXT

    def test_the_doc_the_notice_cites_is_a_file_in_the_repo(self):
        cited_paths = {match for text in (NOTICE_SCREEN_TEXT, NOTICE_AGENT_TEXT) for match in CITED_DOC_PATH.findall(text)}

        assert cited_paths == {"docs/session-config-keys-log.md"}
        assert (REPO_ROOT / "docs/session-config-keys-log.md").is_file()


class TestLogStaysOutOfGit:
    def test_a_log_written_into_the_checkout_is_gitignored(self, tmp_path):
        if shutil.which("git") is None:
            pytest.skip("git not found in PATH")
        inside_work_tree = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            check=False,
        )
        if inside_work_tree.returncode != 0:
            pytest.skip("the repo root is not inside a git work tree")

        check_ignore = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "check-ignore", "-v", f"claude/.claude/{LOG_FILENAME}"],
            capture_output=True,
            text=True,
            env={**os.environ, **git_isolation_env(tmp_path)},
            check=False,
        )

        assert check_ignore.returncode == 0, "git check-ignore exits 1 when the path is not ignored"
        # `-v` prints `<source>:<line>:<pattern><TAB><path>`; the source must be the repo's own .gitignore.
        matched_rule, _, _ = check_ignore.stdout.partition("\t")
        assert matched_rule.split(":")[0] == ".gitignore", check_ignore.stdout


# --------------------------------------------------------------------------- #
# Row-writing cases (2-11)                                                    #
# --------------------------------------------------------------------------- #


NON_ASCII_VALUE = "café"


@requires_jq
class TestRowContent:
    @pytest.mark.parametrize("layout", ["config-outside-home-claude", "config-is-home-claude", "config-path-with-space"])
    def test_every_key_shows_its_no_state_value_and_the_row_stays_under_the_size_bound(self, tmp_path, layout):
        """Positive control for `unreadable`: nothing here is unreadable, and both true and false occur."""
        config_dir_name = {
            "config-outside-home-claude": "config",
            "config-is-home-claude": "home/.claude",
            "config-path-with-space": "config dir",
        }[layout]
        sandbox = make_sandbox(tmp_path, config_dir_name=config_dir_name)
        expected_keys = no_state_values(SHIPPED_SCHEMA_TEXT)
        assert UNREADABLE not in expected_keys.values()
        assert {"true", "false"} <= set(expected_keys.values())

        run = run_hook(sandbox)

        row = assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )
        row_line = sandbox.log_path.read_text().strip("\n")
        assert row_line == json.dumps(row, separators=(",", ":"))
        # Worst case: source and every value unreadable, with the harness-shaped 36-character session id.
        worst_case = {**row, "source": UNREADABLE, "keys": dict.fromkeys(row["keys"], UNREADABLE)}
        worst_case_bytes = json.dumps(worst_case, separators=(",", ":")).encode()
        assert len(worst_case_bytes) + 2 < ROW_SIZE_BOUND_BYTES

    def test_a_flipped_bool_key_and_an_enabled_enum_key_change_and_the_rest_keep_their_values(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        bool_row = first_row(SHIPPED_SCHEMA_TEXT, type="bool", resolution="config-dir")
        enum_row = next((row for row in schema_rows(SHIPPED_SCHEMA_TEXT) if row["type"].startswith("enum:")), None)
        assert enum_row is not None, "schema has no enum: key"
        flipped_bool = "false" if bool_row["default"] == "true" else "true"
        enum_literal = enum_row["type"].removeprefix("enum:")
        (sandbox.config_dir / "claude-config.toml").write_text(
            f'{bool_row["key"]} = {flipped_bool}\n{enum_row["key"]} = "{enum_literal}"\n'
        )
        expected_keys = {
            **no_state_values(SHIPPED_SCHEMA_TEXT),
            bool_row["key"]: flipped_bool,
            enum_row["key"]: enum_literal,
        }

        run = run_hook(sandbox)

        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    def test_legacy_sentinels_resolve_by_the_polarity_column(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        enables_row = first_row(SHIPPED_SCHEMA_TEXT, resolution="config-dir", legacy_polarity="presence-enables")
        disables_row = first_row(SHIPPED_SCHEMA_TEXT, legacy_polarity="presence-disables")
        (sandbox.config_dir / enables_row["legacy_filename"]).write_text("")
        (sandbox.config_dir / disables_row["legacy_filename"]).write_text("")
        expected_keys = {
            **no_state_values(SHIPPED_SCHEMA_TEXT),
            enables_row["key"]: "true",
            disables_row["key"]: "false",
        }

        run = run_hook(sandbox)

        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    @pytest.mark.parametrize("sentinel_location", ["under-home-claude-only", "under-config-dir-only"])
    def test_config_dir_or_home_key_keeps_the_union_a_gate_sees(self, tmp_path, sentinel_location):
        """Fails a hook that passes the config dir as `_config_value`'s override, which skips the union."""
        sandbox = make_sandbox(tmp_path)
        union_row = first_row(
            SHIPPED_SCHEMA_TEXT, resolution="config-dir-or-home", legacy_polarity="presence-enables"
        )
        sentinel_dir = sandbox.home / ".claude" if sentinel_location == "under-home-claude-only" else sandbox.config_dir
        sentinel_dir.mkdir(parents=True, exist_ok=True)
        (sentinel_dir / union_row["legacy_filename"]).write_text("")
        expected_keys = {**no_state_values(SHIPPED_SCHEMA_TEXT), union_row["key"]: "true"}

        run = run_hook(sandbox)

        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    def test_warning_state_leaves_every_key_at_its_no_state_value_and_no_path_in_the_row(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        state_file = sandbox.config_dir / "claude-config.toml"
        state_file.write_text("this line has no equals sign\nnot_a_schema_key = true\n")
        control_key = first_row(SHIPPED_SCHEMA_TEXT, resolution="config-dir")["key"]

        control = run_bare(sandbox, HOOKS_DIR, f"_config_value {control_key}")
        run = run_hook(sandbox)

        assert str(state_file) in control.stderr
        row = assert_row_appended(
            sandbox,
            run,
            b"",
            expected_source="startup",
            expected_keys=no_state_values(SHIPPED_SCHEMA_TEXT),
            expected_non_blank_lines=1,
        )
        assert str(sandbox.config_dir) not in json.dumps(row)

    @pytest.mark.parametrize("position", ["first", "middle", "last"])
    def test_one_failing_key_records_unreadable_for_that_key_only(self, tmp_path, position):
        sandbox = make_sandbox(tmp_path)
        keys = shipped_keys()
        blanked_key = {"first": keys[0], "middle": keys[len(keys) // 2], "last": keys[-1]}[position]
        schema_text = schema_with_row_edit(SHIPPED_SCHEMA_TEXT, blanked_key, "default", "")
        hook = isolated_hook(tmp_path, schema_text)
        control = run_bare(sandbox, hook.parent, f"_config_value {blanked_key}")
        expected_keys = {**no_state_values(SHIPPED_SCHEMA_TEXT), blanked_key: UNREADABLE}

        run = run_hook(sandbox, hook=hook)

        assert str(hook.parent / "config-keys.psv") in control.stderr
        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    @pytest.mark.parametrize(
        ("default_value", "expected_value"),
        [
            pytest.param("/tmp/x", UNREADABLE, id="path"),
            pytest.param("two words", UNREADABLE, id="free-text"),
            pytest.param("ABC", UNREADABLE, id="uppercase"),
            pytest.param("café", UNREADABLE, id="non-ascii"),
            pytest.param("a-b_9", "a-b_9", id="positive-control-token"),
        ],
    )
    def test_token_check_keeps_paths_and_free_text_out_of_a_raw_schema_default(
        self, tmp_path, default_value, expected_value
    ):
        """An empty-polarity key with no state returns its raw schema default, so the token check is the only guard."""
        sandbox = make_sandbox(tmp_path)
        raw_default_row = first_row(SHIPPED_SCHEMA_TEXT, resolution="config-dir", legacy_polarity="")
        schema_text = schema_with_row_edit(SHIPPED_SCHEMA_TEXT, raw_default_row["key"], "default", default_value)
        hook = isolated_hook(tmp_path, schema_text)
        expected_keys = {**no_state_values(SHIPPED_SCHEMA_TEXT), raw_default_row["key"]: expected_value}

        run = run_hook(sandbox, hook=hook)

        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    def test_non_ascii_source_and_value_record_unreadable_where_a_range_class_would_accept_them(self, tmp_path):
        """The hook spells out the token class so no locale's collation widens it; an absent widening locale fails under CI."""
        widening_locale = installed_locale_where(
            '[[ "$1" =~ ^[a-z]+$ ]]',
            NON_ASCII_VALUE,
            absent_message="no installed UTF-8 locale makes bash's [a-z] match a non-ASCII letter",
        )
        sandbox = make_sandbox(tmp_path)
        raw_default_row = first_row(SHIPPED_SCHEMA_TEXT, resolution="config-dir", legacy_polarity="")
        schema_text = schema_with_row_edit(SHIPPED_SCHEMA_TEXT, raw_default_row["key"], "default", NON_ASCII_VALUE)
        hook = isolated_hook(tmp_path, schema_text)
        expected_keys = {**no_state_values(SHIPPED_SCHEMA_TEXT), raw_default_row["key"]: UNREADABLE}

        run = run_hook(sandbox, payload_json(source=NON_ASCII_VALUE), hook=hook, extra_env={"LC_ALL": widening_locale})

        assert_row_appended(
            sandbox, run, b"", expected_source=UNREADABLE, expected_keys=expected_keys, expected_non_blank_lines=1
        )

    def test_a_key_added_to_the_schema_is_recorded_with_no_hook_edit(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        added_columns = ["synthetic_added_key", "bool", "true", "config-dir", "false", "config-dir", "", ""]
        added_row = "|".join([*added_columns, "Synthetic key", "docs/hooks.md", ""])
        schema_text = SHIPPED_SCHEMA_TEXT + added_row + "\n"
        hook = isolated_hook(tmp_path, schema_text)
        expected_keys = {**no_state_values(SHIPPED_SCHEMA_TEXT), "synthetic_added_key": "true"}

        run = run_hook(sandbox, hook=hook)

        assert_row_appended(
            sandbox, run, b"", expected_source="startup", expected_keys=expected_keys, expected_non_blank_lines=1
        )

    def test_a_duplicated_schema_row_yields_one_pair_holding_the_first_rows_value(self, tmp_path):
        """Pins the library's first-row resolution and the encode's object shape; the hook has no dedup code."""
        sandbox = make_sandbox(tmp_path)
        duplicated = first_row(SHIPPED_SCHEMA_TEXT, legacy_polarity="")
        flipped_default = "true" if duplicated["default"] == "false" else "false"
        duplicate_row = "|".join({**duplicated, "default": flipped_default}.values())
        hook = isolated_hook(tmp_path, SHIPPED_SCHEMA_TEXT + duplicate_row + "\n")

        run = run_hook(sandbox, hook=hook)

        assert_silent_success(run)
        # object_pairs_hook keeps every pair, which a plain json.loads would collapse.
        top_level_pairs = json.loads(sandbox.log_path.read_text().strip("\n"), object_pairs_hook=list)
        key_pairs = dict(top_level_pairs)["keys"]
        assert [value for key, value in key_pairs if key == duplicated["key"]] == [duplicated["default"]]


@requires_jq
class TestSourceAndAppend:
    def test_four_events_append_four_rows_in_order_each_after_one_blank_line(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        expected_keys = no_state_values(SHIPPED_SCHEMA_TEXT)
        sources = ["startup", "clear", "compact", "resume"]
        logged_at_values = []

        earlier_bytes = b""
        for run_number, source in enumerate(sources, start=1):
            run = run_hook(sandbox, payload_json(source=source))
            row = assert_row_appended(
                sandbox,
                run,
                earlier_bytes,
                expected_source=source,
                expected_keys=expected_keys,
                expected_non_blank_lines=run_number,
            )
            logged_at_values.append(row["logged_at"])
            earlier_bytes = sandbox.log_path.read_bytes()

        row_lines = non_blank_lines(earlier_bytes.decode())
        assert earlier_bytes.decode() == "".join(f"\n{line}\n" for line in row_lines)
        assert [json.loads(line)["source"] for line in row_lines] == sources
        parsed_times = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in logged_at_values]
        assert parsed_times == sorted(parsed_times)

    @pytest.mark.parametrize(
        "payload_source",
        [
            pytest.param(None, id="absent"),
            pytest.param("two words", id="free-text"),
            pytest.param("/tmp/x", id="path"),
            pytest.param("ABC", id="uppercase"),
            pytest.param("café", id="non-ascii"),
            pytest.param("a\nb", id="embedded-newline"),
        ],
    )
    def test_a_source_that_is_not_a_plain_token_records_unreadable(self, tmp_path, payload_source):
        """The embedded-newline variant fails a check that matches line by line."""
        sandbox = make_sandbox(tmp_path)

        run = run_hook(sandbox, payload_json(source=payload_source))

        assert_row_appended(
            sandbox,
            run,
            b"",
            expected_source=UNREADABLE,
            expected_keys=no_state_values(SHIPPED_SCHEMA_TEXT),
            expected_non_blank_lines=1,
        )


@requires_jq
class TestLogFile:
    def test_a_new_log_is_created_at_mode_600(self, tmp_path):
        sandbox = make_sandbox(tmp_path)

        run = run_hook(sandbox)

        assert_silent_success(run)
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600

    def test_a_seeded_log_at_mode_644_ends_at_600_with_its_line_intact(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o644)

        run = run_hook(sandbox)

        assert_row_appended(
            sandbox,
            run,
            seed,
            expected_source="startup",
            expected_keys=no_state_values(SHIPPED_SCHEMA_TEXT),
            expected_non_blank_lines=2,
        )

    def test_umask_077_alone_yields_mode_600_when_chmod_changes_nothing(self, tmp_path):
        """Only the create-time mode can produce 600 here: the chmod stub exits 0 without changing a mode."""
        sandbox = make_sandbox(tmp_path)
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, path=stubs.path, umask=0o022)

        assert_silent_success(run)
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600

    def test_a_config_dir_reached_through_a_symlink_still_gets_its_row(self, tmp_path):
        """The guard checks only the log path's last component, so a symlinked account directory must not lose rows."""
        sandbox = make_sandbox(tmp_path, create_config_dir=False)
        real_config_dir = tmp_path / "real-config"
        real_config_dir.mkdir()
        sandbox.config_dir.symlink_to(real_config_dir)

        run = run_hook(sandbox)

        assert_row_appended(
            sandbox,
            run,
            b"",
            expected_source="startup",
            expected_keys=no_state_values(SHIPPED_SCHEMA_TEXT),
            expected_non_blank_lines=1,
        )
        assert (real_config_dir / LOG_FILENAME).read_bytes() == sandbox.log_path.read_bytes()

    def test_an_unset_config_dir_variable_writes_the_row_under_home_dot_claude(self, tmp_path):
        """The default account has no CLAUDE_CONFIG_DIR, so the log path must come from HOME alone."""
        sandbox = make_sandbox(tmp_path, config_dir_name="home/.claude")

        run = run_hook(sandbox, unset=("CLAUDE_CONFIG_DIR",))

        assert_row_appended(
            sandbox,
            run,
            b"",
            expected_source="startup",
            expected_keys=no_state_values(SHIPPED_SCHEMA_TEXT),
            expected_non_blank_lines=1,
        )
        assert sandbox.log_path == sandbox.home / ".claude" / LOG_FILENAME

    def test_concurrent_sessions_each_get_one_parseable_row_in_a_fresh_log(self, tmp_path):
        """End-to-end check that overlapping sessions lose no row, which a rewrite of the log (temp file plus mv) would.

        It does not discriminate a truncating create or a split append: `noclobber` is pinned only by
        `test_the_exclusive_create_keeps_the_bytes_of_a_log_that_appears_after_the_existence_check`,
        and the one-write append by `TestRowReachesTheLogInOneWrite`.
        """
        sandbox = make_sandbox(tmp_path)
        session_ids = [f"concurrent-session-{number:02d}" for number in range(CONCURRENT_SESSION_COUNT)]
        env = hook_env(sandbox)
        stdin_dir = tmp_path / "stdin-payloads"
        stdin_dir.mkdir()

        processes = []
        for session_id in session_ids:
            stdin_path = stdin_dir / f"{session_id}.json"
            stdin_path.write_text(payload_json(session_id=session_id))
            with stdin_path.open() as stdin_file:
                processes.append(
                    subprocess.Popen(
                        [str(HOOK)],
                        stdin=stdin_file,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        cwd=sandbox.workdir,
                        env=env,
                    )
                )
        outputs = [process.communicate(timeout=HOOK_TIMEOUT_SECONDS) for process in processes]

        assert [process.returncode for process in processes] == [0] * CONCURRENT_SESSION_COUNT
        assert outputs == [("", "")] * CONCURRENT_SESSION_COUNT
        row_lines = non_blank_lines(sandbox.log_path.read_text())
        logged_session_ids = [json.loads(line)["session_id"] for line in row_lines]
        assert sorted(logged_session_ids) == session_ids
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600

    def test_an_unfinished_last_line_costs_no_later_row(self, tmp_path):
        """Every append starts with a newline, so a row never fuses with a line an earlier write left unfinished."""
        sandbox = make_sandbox(tmp_path)
        seed = b'{"session_id":"earlier","sou'
        sandbox.log_path.write_bytes(seed)

        run = run_hook(sandbox)

        assert_silent_success(run)
        after = sandbox.log_path.read_bytes()
        seed_line, row_line, trailing = after.decode().split("\n")
        assert after.startswith(seed + b"\n")
        assert after.endswith(b"\n")
        assert trailing == ""
        with pytest.raises(json.JSONDecodeError):
            json.loads(seed_line)
        assert json.loads(row_line)["session_id"] == SESSION_ID


# --------------------------------------------------------------------------- #
# No-row cases (12-23)                                                        #
# --------------------------------------------------------------------------- #


@requires_jq
class TestPayloadGates:
    @pytest.mark.parametrize(
        "stdin_text",
        [
            pytest.param("", id="empty-stdin"),
            pytest.param("{not json", id="malformed"),
            pytest.param("[]", id="array"),
            pytest.param("null", id="null"),
            pytest.param('"x"', id="bare-string"),
            pytest.param(json.dumps({"session_id": SESSION_ID, "source": "startup"}), id="no-event-name"),
            pytest.param(json.dumps({"hook_event_name": "SessionStart", "source": "startup"}), id="no-session-id"),
            pytest.param(payload_json(session_id="../escape"), id="session-id-path-traversal"),
            pytest.param(payload_json(hook_event_name="SubagentStart"), id="other-event"),
        ],
    )
    def test_payload_the_hook_cannot_use_writes_no_row_and_prints_the_notice(self, tmp_path, stdin_text):
        sandbox = make_sandbox(tmp_path)
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, stdin_text, path=stubs.path)

        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)
        assert stubs.encode_call_count() == 0


@requires_jq
class TestConfigDirUnresolvable:
    @pytest.mark.parametrize(
        "env_options",
        [
            pytest.param({"extra_env": {"CLAUDE_CONFIG_DIR": "relative-config"}}, id="relative-config-dir"),
            # The library resolves "/" to an empty directory, which would put the log at the filesystem root.
            pytest.param({"extra_env": {"CLAUDE_CONFIG_DIR": "/"}}, id="config-dir-is-filesystem-root"),
            # The library strips one trailing slash, so "//" resolves to "/" and would put the log at the filesystem root.
            pytest.param({"extra_env": {"CLAUDE_CONFIG_DIR": "//"}}, id="config-dir-is-slashes-only"),
            pytest.param({"unset": ("HOME", "CLAUDE_CONFIG_DIR")}, id="home-and-config-dir-unset"),
            pytest.param({"extra_env": {"HOME": ""}, "unset": ("CLAUDE_CONFIG_DIR",)}, id="home-empty"),
        ],
    )
    def test_no_config_dir_writes_no_row_anywhere_and_prints_the_notice(self, tmp_path, env_options):
        sandbox = make_sandbox(tmp_path)
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, path=stubs.path, **env_options)

        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)
        assert stubs.encode_call_count() == 0


@requires_jq
class TestNoKeyList:
    @pytest.mark.parametrize(
        "schema_text",
        [pytest.param(None, id="schema-absent"), pytest.param("# only a comment\n", id="comments-only-schema")],
    )
    def test_no_key_list_writes_no_row_and_prints_the_notice(self, tmp_path, schema_text):
        sandbox = make_sandbox(tmp_path)
        stubs = install_stubs(tmp_path / "stubs")
        hook = isolated_hook(tmp_path, schema_text)
        control = run_bare(sandbox, hook.parent, "_config_schema_known_keys")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert control.returncode != 0
        assert str(hook.parent / "config-keys.psv") in control.stderr
        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)
        assert stubs.encode_call_count() == 0


@requires_jq
class TestLibraryUnavailable:
    def test_hook_without_its_lib_prints_the_notice_and_writes_no_row(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        lone_dir = tmp_path / "lone"
        lone_dir.mkdir()
        (lone_dir / HOOK.name).symlink_to(HOOK)
        control = subprocess.run(
            ["bash", "-c", '. "$1/_lib.sh"', "_", str(lone_dir)], capture_output=True, text=True, check=False
        )

        run = run_hook(sandbox, hook=lone_dir / HOOK.name)

        assert control.returncode != 0
        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)

    def test_hook_without_jq_prints_the_notice_and_writes_no_row(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        farm_dir = tmp_path / "path-farm"
        farm_dir.mkdir()
        path_without_jq = build_path_without("jq", farm_dir)
        control = run_bare(sandbox, HOOKS_DIR, "_lib_jq -n 1", path=path_without_jq)

        run = run_hook(sandbox, path=path_without_jq)

        assert control.returncode != 0
        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)


@requires_jq
class TestEncodeFailure:
    @pytest.mark.parametrize("jq_encode", ["partial_output_then_exit_1", "no_output_exit_0"])
    def test_a_failed_row_encode_writes_no_row_and_prints_only_the_notice(self, tmp_path, jq_encode):
        """The no-output variant is stopped only by the hook's `[ -n "$row" ]` guard."""
        sandbox = make_sandbox(tmp_path)
        stubs = install_stubs(tmp_path / "stubs", jq_encode=jq_encode)

        run = run_hook(sandbox, path=stubs.path)

        assert stubs.encode_call_count() == 1
        assert_notice_only(run)
        assert_no_log_anywhere(tmp_path)


# Replaces the `[` builtin for tests on the log path only. `builtin [` in the match and
# the pass-through avoids recursion, and `${1-}` survives the hook's `set -u`. A function named `[`
# receives the closing `]` as its last argument, so the pass-through hands `"$@"` on whole.
BRACKET_SEAM_TEMPLATE = r"""
[() {
  if __MATCH_CONDITION__; then
    builtin printf 'intercepted\n' >> "__INTERCEPT_LOG__"
    return __REPORTED_STATUS__
  fi
  builtin [ "$@"
}
"""
# Each variant is the match condition for one call form of the hook's guard and the status the seam reports.
BRACKET_SEAM_VARIANTS = {
    # `[ -O log ]` reports the log as owned by someone else, which a single-uid run cannot build with chown.
    "log-not-owned": ('builtin [ "${1-}" = -O ] && builtin [ "${2-}" = "__LOG_PATH__" ]', "1"),
    # `[ ! -e log ]` holds, so the hook's create runs against a log that exists.
    "log-reported-absent": (
        'builtin [ "${1-}" = "!" ] && builtin [ "${2-}" = -e ] && builtin [ "${3-}" = "__LOG_PATH__" ]',
        "0",
    ),
    # Matches nothing: proves the seam leaves the hook working when it intercepts nothing.
    "never-matches": ('builtin [ "${1-}" = __no_such_operand__ ]', "0"),
}


def write_bracket_seam(tmp_path: Path, log_path: Path, variant: str) -> tuple[Path, Path]:
    """The seam file for BASH_ENV, and the file that records each interception."""
    match_condition, reported_status = BRACKET_SEAM_VARIANTS[variant]
    intercept_log = tmp_path / "bracket-intercepts.log"
    seam_file = tmp_path / "bracket-seam.sh"
    seam_file.write_text(
        BRACKET_SEAM_TEMPLATE.replace("__MATCH_CONDITION__", match_condition)
        .replace("__LOG_PATH__", str(log_path))
        .replace("__INTERCEPT_LOG__", str(intercept_log))
        .replace("__REPORTED_STATUS__", reported_status)
    )
    return seam_file, intercept_log


@requires_jq
class TestWriteGuard:
    """Runs against the shipped header and its first key row only, so none depends on a key's value.

    Each case also proves its run reached the write guard: the jq spy's call log holds the encode call.

    A log owned by another account cannot be built in single-uid CI, so the ownership and exclusive-create
    cases report a state through a `[` function in BASH_ENV instead. Each fails when its guard is removed
    from the hook.
    """

    @pytest.fixture
    def sandbox(self, tmp_path) -> Sandbox:
        return make_sandbox(tmp_path)

    @pytest.fixture
    def hook(self, tmp_path) -> Path:
        return isolated_hook(tmp_path, ONE_ROW_SCHEMA_TEXT)

    def test_control_with_nothing_at_the_log_path_writes_the_one_key_row(self, tmp_path, sandbox, hook):
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_row_appended(
            sandbox,
            run,
            b"",
            expected_source="startup",
            expected_keys=one_row_expected_keys(),
            expected_non_blank_lines=1,
        )
        assert stubs.encode_call_count() == 1

    def test_symlink_at_the_log_path_is_not_followed(self, tmp_path, sandbox, hook):
        target = tmp_path / "seeded-target.jsonl"
        target.write_bytes(b"seed\n")
        target.chmod(0o644)
        sandbox.log_path.symlink_to(target)
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert sandbox.log_path.is_symlink()
        assert_log_untouched(target, expected_bytes=b"seed\n", expected_mode=0o644)
        assert stubs.chmod_call_lines() == []

    def test_dangling_symlink_at_the_log_path_never_creates_its_target(self, tmp_path, sandbox, hook):
        target = tmp_path / "never-created.jsonl"
        sandbox.log_path.symlink_to(target)
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert sandbox.log_path.is_symlink()
        assert not target.exists()
        assert stubs.chmod_call_lines() == []

    def test_fifo_at_the_log_path_is_never_opened(self, tmp_path, sandbox, hook):
        os.mkfifo(sandbox.log_path)
        sandbox.log_path.chmod(0o644)
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert stat.S_ISFIFO(sandbox.log_path.lstat().st_mode)
        assert stat.S_IMODE(sandbox.log_path.lstat().st_mode) == 0o644
        assert stubs.chmod_call_lines() == []

    def test_directory_at_the_log_path_keeps_its_mode(self, tmp_path, sandbox, hook):
        sandbox.log_path.mkdir()
        sandbox.log_path.chmod(0o755)
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert sandbox.log_path.is_dir()
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o755

    def test_control_the_bracket_seam_intercepting_nothing_still_writes_the_row(self, tmp_path, sandbox, hook):
        """Proves the `[` function passes every other test through, so the two cases below fail on the hook."""
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o644)
        seam_file, intercept_log = write_bracket_seam(tmp_path, sandbox.log_path, "never-matches")
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, hook=hook, path=stubs.path, bash_env_file=seam_file)

        assert not intercept_log.exists()
        assert_row_appended(
            sandbox,
            run,
            seed,
            expected_source="startup",
            expected_keys=one_row_expected_keys(),
            expected_non_blank_lines=2,
        )

    def test_a_log_the_account_does_not_own_is_left_alone_and_no_mode_is_set(self, tmp_path, sandbox, hook):
        """Fails when the hook's `[ -O ]` check is removed: another account's pre-created log would get rows."""
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o644)
        seam_file, intercept_log = write_bracket_seam(tmp_path, sandbox.log_path, "log-not-owned")
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, hook=hook, path=stubs.path, bash_env_file=seam_file)

        assert_seam_intercepted(intercept_log, guard="the hook's `[ -O log ]` ownership check")
        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert_log_untouched(sandbox.log_path, expected_bytes=seed, expected_mode=0o644)
        assert stubs.chmod_call_lines() == []

    def test_the_exclusive_create_keeps_the_bytes_of_a_log_that_appears_after_the_existence_check(
        self, tmp_path, sandbox, hook
    ):
        """The sole pin of `set -o noclobber` in the hook's create: without it `: >` truncates the seed."""
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o600)
        seam_file, intercept_log = write_bracket_seam(tmp_path, sandbox.log_path, "log-reported-absent")
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, hook=hook, path=stubs.path, bash_env_file=seam_file)

        assert_seam_intercepted(intercept_log, guard="the hook's `[ ! -e log ]` existence check before its create")
        assert_row_appended(
            sandbox,
            run,
            seed,
            expected_source="startup",
            expected_keys=one_row_expected_keys(),
            expected_non_blank_lines=2,
        )

    def test_a_mode_that_cannot_be_set_writes_no_row_into_a_seeded_log(self, tmp_path, sandbox, hook):
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o644)
        stubs = install_stubs(tmp_path / "stubs", chmod="fail")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert_log_untouched(sandbox.log_path, expected_bytes=seed, expected_mode=0o644)
        assert any(line.endswith(str(sandbox.log_path)) for line in stubs.chmod_call_lines())

    def test_a_mode_that_cannot_be_set_leaves_the_new_empty_log_behind(self, tmp_path, sandbox, hook):
        """The reader doc reads a log with no row as no log."""
        stubs = install_stubs(tmp_path / "stubs", chmod="fail")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert sandbox.log_path.is_file()
        assert_log_untouched(sandbox.log_path, expected_bytes=b"", expected_mode=0o600)
        assert any(line.endswith(str(sandbox.log_path)) for line in stubs.chmod_call_lines())

    def test_a_missing_config_dir_writes_nothing_at_that_path(self, tmp_path, hook):
        sandbox = make_sandbox(tmp_path, config_dir_name="missing-config", create_config_dir=False)
        stubs = install_stubs(tmp_path / "stubs")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert not sandbox.config_dir.exists()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_a_refused_append_and_its_retry_write_no_row_and_print_the_notice(self, tmp_path, sandbox, hook):
        """The proof against a real refused write; the every-attempt retry case pins the same outcome on any uid."""
        seed = b'{"seeded": "line"}\n'
        sandbox.log_path.write_bytes(seed)
        sandbox.log_path.chmod(0o400)
        stubs = install_stubs(tmp_path / "stubs", chmod="noop")

        run = run_hook(sandbox, hook=hook, path=stubs.path)

        assert_notice_only(run)
        assert stubs.encode_call_count() == 1
        assert_log_untouched(sandbox.log_path, expected_bytes=seed, expected_mode=0o400)
        assert any(line.endswith(str(sandbox.log_path)) for line in stubs.chmod_call_lines())


@requires_jq
class TestBelowBashFloor:
    @requires_sub_floor_bash
    def test_a_below_floor_bash_prints_the_notice_and_leaves_the_log_alone(self, tmp_path):
        sandbox = make_sandbox(tmp_path)
        control = run_hook(sandbox)
        assert_silent_success(control)
        control_bytes = sandbox.log_path.read_bytes()
        assert len(non_blank_lines(control_bytes.decode())) == 1
        sub_floor_bin = tmp_path / "sub-floor-bin"
        sub_floor_bin.mkdir()
        (sub_floor_bin / "bash").symlink_to(SUB_FLOOR_BASH)

        run = run_hook(sandbox, path=path_in_front_of_real_path(sub_floor_bin))

        assert_notice_only(run)
        assert sandbox.log_path.read_bytes() == control_bytes


# Replaces the printf builtin for the hook's append only. `builtin printf` in the log write and the
# pass-through avoids recursion, `${1-}` survives the hook's `set -u`, and the match string is a raw
# backslash-n because the hook's format argument is the two characters backslash and n.
PRINTF_SEAM_TEMPLATE = r"""
printf() {
  if [ "${1-}" != '\n%s\n' ]; then
    builtin printf "$@"
    return
  fi
  RETRY_SEAM_CALLS=$(( ${RETRY_SEAM_CALLS:-0} + 1 ))
  builtin printf 'intercepted\n' >> "__INTERCEPT_LOG__"
  __VARIANT_BODY__
}
"""
APPEND_PRINTF_GUARD = "the hook's `printf '\\n%s\\n'` append and its retry"
PRINTF_SEAM_VARIANT_BODIES = {
    # First attempt writes a newline and the row's first seven bytes, then fails.
    "partial": (
        'if [ "$RETRY_SEAM_CALLS" = 1 ]; then builtin printf \'\\n%s\' "${2:0:7}"; return 1; fi\n'
        '  builtin printf "$@"'
    ),
    "empty": 'if [ "$RETRY_SEAM_CALLS" = 1 ]; then return 1; fi\n  builtin printf "$@"',
    "every-attempt": "return 1",
}


@requires_jq
class TestRetriedAppend:
    """The only pin of the retry and of its leading newline. If the seam intercepts fewer than two calls
    the function never took effect, and the case fails instead of passing vacuously."""

    SEED = b'{"seeded": "line"}\n'

    def _run_with_seam(self, tmp_path: Path, variant: str) -> tuple[Sandbox, HookRun, Path]:
        sandbox = make_sandbox(tmp_path)
        sandbox.log_path.write_bytes(self.SEED)
        sandbox.log_path.chmod(0o600)
        intercept_log = tmp_path / "intercepts.log"
        seam_file = tmp_path / "printf-seam.sh"
        seam_file.write_text(
            PRINTF_SEAM_TEMPLATE.replace("__INTERCEPT_LOG__", str(intercept_log)).replace(
                "__VARIANT_BODY__", PRINTF_SEAM_VARIANT_BODIES[variant]
            )
        )
        hook = isolated_hook(tmp_path, ONE_ROW_SCHEMA_TEXT)
        return sandbox, run_hook(sandbox, hook=hook, bash_env_file=seam_file), intercept_log

    def test_a_partial_first_attempt_is_followed_by_a_retry_whose_leading_newline_keeps_the_row_parseable(self, tmp_path):
        sandbox, run, intercept_log = self._run_with_seam(tmp_path, "partial")

        assert_seam_intercepted(intercept_log, guard=APPEND_PRINTF_GUARD, calls=2)
        assert_silent_success(run)
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600
        after = sandbox.log_path.read_bytes()
        row_line = after.split(b"\n")[-2]
        assert after == self.SEED + b'\n{"sessi' + b"\n" + row_line + b"\n"
        _, blank_line, unfinished_line, parsed_row_line, _ = after.decode().split("\n")
        assert blank_line == ""
        with pytest.raises(json.JSONDecodeError):
            json.loads(unfinished_line)
        assert json.loads(parsed_row_line)["session_id"] == SESSION_ID

    def test_an_empty_first_attempt_is_followed_by_a_retry_that_writes_the_row_after_one_blank_line(self, tmp_path):
        sandbox, run, intercept_log = self._run_with_seam(tmp_path, "empty")

        assert_seam_intercepted(intercept_log, guard=APPEND_PRINTF_GUARD, calls=2)
        assert_silent_success(run)
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600
        after = sandbox.log_path.read_bytes()
        row_line = after.split(b"\n")[-2]
        assert after == self.SEED + b"\n" + row_line + b"\n"
        assert json.loads(row_line)["session_id"] == SESSION_ID

    def test_two_failed_attempts_write_no_row_and_print_the_notice_on_any_uid(self, tmp_path):
        sandbox, run, intercept_log = self._run_with_seam(tmp_path, "every-attempt")

        assert_seam_intercepted(intercept_log, guard=APPEND_PRINTF_GUARD, calls=2)
        assert_notice_only(run)
        assert_log_untouched(sandbox.log_path, expected_bytes=self.SEED, expected_mode=0o600)


STRACE_WRITE_FLAGS = ("-f", "-y", "-s", "4096", "-e", "trace=write")
# `-s 4096` is above ROW_SIZE_BOUND_BYTES, so strace prints a row's write through its final newline.
STRACE_WRITE_LINE = re.compile(r'^\d+ +write\(\d+<(?P<path>[^>]+)>, "(?P<data>.*)", (?P<requested>\d+)\) += (?P<written>\d+)$')


@functools.cache
def strace_unavailable_reason() -> str | None:
    """None when a traced run works here, otherwise why the trace case skips."""
    strace = shutil.which("strace")
    true_binary = shutil.which("true")
    if strace is None or true_binary is None:
        return "strace or true not found in PATH"
    with tempfile.TemporaryDirectory() as scratch:
        probe = subprocess.run(
            [strace, *STRACE_WRITE_FLAGS, "-o", f"{scratch}/probe", true_binary],
            capture_output=True,
            check=False,
        )
    return None if probe.returncode == 0 else "strace rejects -y or cannot trace here"


@requires_jq
class TestRowReachesTheLogInOneWrite:
    """The no-lock claim rests on this: a row line under ROW_SIZE_BOUND_BYTES goes out in one write(2)."""

    def test_the_row_line_and_its_newline_go_out_in_one_write_and_no_write_ends_mid_line(self, tmp_path):
        unavailable_reason = strace_unavailable_reason()
        if unavailable_reason is not None:
            pytest.skip(unavailable_reason)
        sandbox = make_sandbox(tmp_path)
        trace_path = tmp_path / "trace"
        strace = shutil.which("strace")
        assert strace is not None

        run = run_hook(sandbox, command_prefix=(strace, *STRACE_WRITE_FLAGS, "-o", str(trace_path)))

        assert_silent_success(run)
        log_bytes = sandbox.log_path.read_bytes()
        # The row encode's jq child writes the same bytes to a pipe, so only writes whose descriptor is the log count.
        log_writes = [
            written
            for line in trace_path.read_text().splitlines()
            if (written := STRACE_WRITE_LINE.match(line)) and written["path"] == str(sandbox.log_path)
        ]
        assert log_writes, "strace saw no write to the log"
        assert all(write["requested"] == write["written"] for write in log_writes)
        assert sum(int(write["written"]) for write in log_writes) == len(log_bytes)
        # No write to the log ends mid-line, so an interleaved writer can only land between whole lines.
        assert all(write["data"].endswith("\\n") for write in log_writes)
        # The row is JSON of tokens, so strace's only escape in it is a backslash before each double quote.
        row_line = log_bytes[1:-1].decode()
        row_and_newline_as_traced = row_line.replace('"', '\\"') + "\\n"
        assert any(write["data"].endswith(row_and_newline_as_traced) for write in log_writes)


def trim_recipe_from_reader_doc(days: int) -> str:
    """The reader doc's trim command with `<days>` filled in, so the case runs the text an engineer would copy."""
    fenced_bash_blocks = re.findall(r"```bash\n(.*?)\n```", READER_DOC.read_text(), flags=re.DOTALL)
    [recipe] = [block for block in fenced_bash_blocks if "jq -cR" in block]
    assert "<days>" in recipe
    return recipe.replace("<days>", str(days))


def run_trim_recipe(
    sandbox: Sandbox, days: int, shell_options: tuple[str, ...] = (), **env_options: Any
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", *shell_options, "-c", trim_recipe_from_reader_doc(days)],
        capture_output=True,
        text=True,
        cwd=sandbox.workdir,
        env=hook_env(sandbox, **env_options),
        timeout=HOOK_TIMEOUT_SECONDS,
        check=False,
    )


@requires_jq
class TestReaderDocTrimRecipe:
    """The recipe's `try` swallows every per-line error, so a drift in the row's `logged_at` would empty the log silently."""

    OUT_OF_WINDOW_LOGGED_AT = "2020-01-01T00:00:00Z"
    TRIM_WINDOW_DAYS = 30

    def _log_after_one_hook_run(self, tmp_path: Path, *, config_dir_name: str = "config") -> tuple[Sandbox, bytes]:
        sandbox = make_sandbox(tmp_path, config_dir_name=config_dir_name)
        assert_silent_success(run_hook(sandbox))
        # Loosen the mode so a final 600 comes from the recipe's own temp file, not from the hook.
        sandbox.log_path.chmod(0o644)
        return sandbox, sandbox.log_path.read_bytes()

    def test_a_row_inside_the_window_survives_and_the_log_ends_at_mode_600(self, tmp_path):
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path)

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS)

        assert trim.returncode == 0, trim.stderr
        [written_row_line] = non_blank_lines(written_bytes.decode())
        assert sandbox.log_path.read_text() == written_row_line + "\n"
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600

    def test_a_row_outside_the_window_is_dropped_and_the_log_ends_at_mode_600(self, tmp_path):
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path)
        written_row = json.loads(non_blank_lines(written_bytes.decode())[0])
        aged_bytes = written_bytes.replace(written_row["logged_at"].encode(), self.OUT_OF_WINDOW_LOGGED_AT.encode())
        sandbox.log_path.write_bytes(aged_bytes)
        assert self.OUT_OF_WINDOW_LOGGED_AT in sandbox.log_path.read_text()

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS)

        assert trim.returncode == 0, trim.stderr
        assert sandbox.log_path.read_bytes() == b""
        assert stat.S_IMODE(sandbox.log_path.stat().st_mode) == 0o600

    def test_a_failing_jq_leaves_the_original_log_in_place(self, tmp_path):
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path)
        failing_bin = tmp_path / "failing-jq-bin"
        failing_bin.mkdir()
        failing_jq = failing_bin / "jq"
        failing_jq.write_text(f"#!{shutil.which('bash')}\nexit 1\n")
        failing_jq.chmod(0o755)

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS, path=path_in_front_of_real_path(failing_bin))

        assert trim.returncode != 0
        assert_log_untouched(sandbox.log_path, expected_bytes=written_bytes, expected_mode=0o644)
        assert list(sandbox.config_dir.iterdir()) == [sandbox.log_path]

    def test_a_row_inside_the_window_survives_in_a_noclobber_shell(self, tmp_path):
        """`mktemp` creates the staging file first, so a plain `>` redirect would be refused under `set -C`."""
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path)

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS, shell_options=("-C",))

        assert trim.returncode == 0, trim.stderr
        [written_row_line] = non_blank_lines(written_bytes.decode())
        assert sandbox.log_path.read_text() == written_row_line + "\n"

    def test_an_unterminated_torn_last_line_is_dropped_and_the_row_inside_the_window_survives(self, tmp_path):
        """jq takes its exit status from the last input line, so only a torn last line shows an unguarded `fromjson`."""
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path)
        [written_row_line] = non_blank_lines(written_bytes.decode())
        sandbox.log_path.write_bytes(written_bytes + b'{"session_id": "torn-')

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS)

        assert trim.returncode == 0, trim.stderr
        assert sandbox.log_path.read_text() == written_row_line + "\n"

    @pytest.mark.parametrize(
        "config_dir_env",
        [
            pytest.param({"unset": ("CLAUDE_CONFIG_DIR",)}, id="unset"),
            pytest.param({"extra_env": {"CLAUDE_CONFIG_DIR": ""}}, id="empty"),
        ],
    )
    def test_an_unset_or_empty_config_dir_variable_trims_the_log_under_home_dot_claude(self, tmp_path, config_dir_env):
        """The recipe's `${CLAUDE_CONFIG_DIR:-$HOME/.claude}` fallback covers both an unset and an empty variable."""
        sandbox, written_bytes = self._log_after_one_hook_run(tmp_path, config_dir_name="home/.claude")
        [written_row_line] = non_blank_lines(written_bytes.decode())

        trim = run_trim_recipe(sandbox, self.TRIM_WINDOW_DAYS, **config_dir_env)

        assert trim.returncode == 0, trim.stderr
        assert sandbox.log_path.read_text() == written_row_line + "\n"
