"""Pure helpers and path constants shared across hook, skill, and script test files.

No pytest decorators here — this is a plain Python module. Import
explicitly from each test file that needs these symbols.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shlex
import shutil
import subprocess
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

import yaml

CLAUDE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CLAUDE_DIR.parent.parent

HOOKS_DIR = CLAUDE_DIR / "hooks"
SKILLS_DIR = REPO_ROOT / "claude-skills" / "skills"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"

_CI_DETECT_STEP_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "tests.yml"

# SKILL.md fences may be indented when the fixture sits inside a
# numbered list (e.g. respond-pr's "0. **Enable hook bypass.**"). The
# closing-fence match has to tolerate the same leading whitespace as
# the opening, otherwise the non-greedy body capture runs past every
# indented fence until it finds an unindented one elsewhere in the file.
_SKILL_FIXTURE_RE = re.compile(
    r"<!--\s*HOOK_TEST_FIXTURE:\s*(?P<id>[A-Za-z0-9_-]+)\b[^>]*-->\s*"
    r"```[a-z]*\n(?P<body>.*?)\n[ \t]*```",
    re.DOTALL,
)


def extract_skill_command(skill_path: Path, fixture_id: str) -> str:
    """Return the body of the fenced code block tagged with `fixture_id`.

    SKILL.md files mark hook-alignment fixtures with
    `<!-- HOOK_TEST_FIXTURE: <id> -->` immediately followed by a fenced
    code block. Reading the recipe from SKILL.md at test time (rather
    than embedding a hardcoded copy in the test source) makes SKILL.md
    the single source of truth — drift between the documented recipe
    and what the test executes can't happen silently.
    """
    text = skill_path.read_text()
    matches = [m for m in _SKILL_FIXTURE_RE.finditer(text) if m.group("id") == fixture_id]
    if not matches:
        raise AssertionError(
            f"HOOK_TEST_FIXTURE '{fixture_id}' not found in {skill_path} — "
            "either the marker was removed or the immediately-following "
            "fenced block is missing."
        )
    if len(matches) > 1:
        raise AssertionError(
            f"HOOK_TEST_FIXTURE '{fixture_id}' appears {len(matches)} times in "
            f"{skill_path} — fixture ids must be unique so the test runs the "
            "intended block."
        )
    return matches[0].group("body").strip()


def _build_subprocess_env(
    home: Path | None,
    extra_env: dict | None,
) -> dict | None:
    """Build a subprocess env with optional HOME override and extra variables.

    Returns None when neither argument is provided, so subprocess.run inherits
    the parent environment as-is — preserving PATH for hook tool lookups
    (jq, grep, git, etc.).

    Full parent env — including any ambient CLAUDE_CONFIG_DIR — is always
    inherited; this function can't distinguish an ambient leak from a test's
    deliberate `monkeypatch.setenv`, so a caller needing it cleared must
    `monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)` before calling.
    """
    if home is None and extra_env is None:
        return None
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    if extra_env is not None:
        env.update(extra_env)
    return env


def run_hook(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> str:
    """Invoke `hook` with `tool_input` as JSON stdin. Return the decision.

    Silent exit 0 (empty stdout) maps to "allow" to match the hook protocol,
    where absence of output means "no opinion". Exit 2 (empty stdout) maps to
    "deny": per the harness's PreToolUse contract, exit 2 is itself the
    blocking signal, delivered via stderr rather than a JSON payload — a gate
    hook's jq-absent fallback takes exactly this path. Without this mapping,
    an exit-2 block with empty stdout would be misread as "allow". A
    non-empty stdout payload is expected to carry
    `hookSpecificOutput.permissionDecision` — missing it raises `KeyError`,
    since for every hook that always emits `hookSpecificOutput` that shape
    break is itself a regression worth a hard test failure. Hooks that
    legitimately emit a decision-less advisory payload (e.g. a PostToolUse
    `systemMessage`, or a `hookSpecificOutput.additionalContext` with no
    `permissionDecision` key at all) should use `run_hook_advisory` instead,
    which treats that absence as "no opinion" rather than a broken payload.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return "deny" if result.returncode == 2 else "allow"
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["permissionDecision"]


def run_hook_advisory(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> str:
    """Like `run_hook`, but for hooks documented as PostToolUse/informational,
    where a non-empty stdout payload may carry only an advisory field (e.g.
    `systemMessage`) with no `hookSpecificOutput.permissionDecision` at all —
    that absence means "no opinion" per the hook protocol, not a broken
    payload shape. Use `run_hook` instead for hooks that always emit
    `hookSpecificOutput`, so a shape regression there still surfaces as a
    hard failure rather than silently defaulting to "allow".

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return "allow"
    payload = json.loads(result.stdout)
    return payload.get("hookSpecificOutput", {}).get("permissionDecision", "allow")


def run_hook_reason(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> str | None:
    """Like `run_hook` but returns the deny `permissionDecisionReason` string
    (or `None` if the hook allowed silently). Used by tests that need to
    assert on the contents of the deny message, not just the decision.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"].get("permissionDecisionReason")


def run_hook_context(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> str | None:
    """Like `run_hook_reason` but returns the allow
    `hookSpecificOutput.additionalContext` string (or `None` if the hook
    allowed silently, with no additionalContext at all). Used by tests that
    need to assert on the contents of an informational allow-path note.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"].get("additionalContext")


def run_hook_payload(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> dict | None:
    """Like `run_hook` but returns the full `hookSpecificOutput` dict. Used
    by tests that need to assert on more than one field from a single
    invocation -- e.g. both `permissionDecision` and `additionalContext` --
    without a second, branch-diverging call.

    Empty stdout is ambiguous, so the return value branches on the exit code:

    - Exit 0: returns `None`. There is no `hookSpecificOutput` to return.
    - Exit 2: returns `{"permissionDecision": "deny"}`, a minimal
      stand-in for the PreToolUse block signal (e.g. a gate hook's
      jq-absent fallback) that omits the `hookEventName`/
      `permissionDecisionReason` fields a real `_lib_emit_deny` payload
      carries.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return {"permissionDecision": "deny"} if result.returncode == 2 else None
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]


def run_hook_stop(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> dict | None:
    """Stop-specific runner. A Stop hook's block payload is a top-level
    {"decision": "block", "reason": ...} pair — distinct from run_hook's
    PreToolUse hookSpecificOutput.permissionDecision (KeyError on this
    shape) and run_hook_advisory's "no opinion" default (a Stop hook has no
    allow/deny axis at all, only "block this turn from ending" or silence,
    so mapping silence to "allow" would misrepresent what the hook did).

    Returns the parsed payload dict when the hook blocks, or None when it
    stays silent (empty stdout). Asserts the exact {"decision", "reason"}
    key pair on any non-empty payload — the harness routes on those literal
    keys, so a typo in the emitting hook must fail loudly here rather than
    silently no-op in production.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    assert set(payload.keys()) == {"decision", "reason"}, (
        f"Stop hook emitted unexpected keys {sorted(payload.keys())} — "
        'the harness routes on the exact {"decision", "reason"} pair'
    )
    assert payload["decision"] == "block", (
        f'Stop hook emitted decision={payload["decision"]!r}, expected "block" '
        "— the harness's Stop contract only recognizes that value"
    )
    return payload


class _NoUpdatedOutputSentinel:
    """Distinct from Python `None`: `run_hook_updated_output` returns this
    when the hook produced no stdout at all (its fail-open passthrough
    path), never for a hook that explicitly emits
    `updatedToolOutput: null`. `json.loads` collapses "no output" and "JSON
    null" to the same Python `None` if the helper returns `None` for both,
    which would let a hook that switches from silent passthrough to an
    explicit-null emission — turning "leave content untouched" into
    "replace the tool result with null", a materially different and more
    dangerous outcome depending on harness semantics — pass any test
    written against `is None`."""

    def __repr__(self) -> str:
        return "NO_UPDATED_OUTPUT"


NO_UPDATED_OUTPUT = _NoUpdatedOutputSentinel()


def run_hook_updated_output(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
):
    """Like `run_hook`, but for a PostToolUse hook whose only success-path
    field is `hookSpecificOutput.updatedToolOutput` — no `permissionDecision`
    is ever emitted for this shape, so `run_hook`/`run_hook_advisory` (which
    key on `permissionDecision`) can't express "did it redact, and to what".

    Returns the parsed `updatedToolOutput` value (any JSON type — an object,
    a bare string, a number, `null` as Python `None`) on a non-empty stdout
    payload, or the `NO_UPDATED_OUTPUT` sentinel when the hook produced no
    output at all (its fail-open path: the harness reads silence as "no
    change", i.e. the original tool_response passed through untouched).
    Callers asserting fail-open passthrough must check `is NO_UPDATED_OUTPUT`,
    not `is None` — the latter also matches a hook that explicitly emitted
    `updatedToolOutput: null`, a different and non-equivalent outcome.

    home: when set, overrides $HOME in the subprocess environment so the
    hook reads its optional additions file from an isolated temp directory
    rather than real ~/.claude.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return NO_UPDATED_OUTPUT
    payload = json.loads(result.stdout)
    return payload["hookSpecificOutput"]["updatedToolOutput"]


def run_hook_session_start(
    hook: Path,
    tool_input: dict,
    cwd: Path | None = None,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> str | None:
    """SessionStart-specific runner. A title-setting SessionStart hook's
    payload is `{"hookSpecificOutput": {"hookEventName": "SessionStart",
    "sessionTitle": ...}}` — distinct from run_hook's PreToolUse
    permissionDecision shape and run_hook_stop's {"decision", "reason"} pair.

    Returns the emitted title string, or None when the hook stays silent
    (empty stdout). Asserts the exact {"hookEventName", "sessionTitle"} key
    set on any non-empty payload's hookSpecificOutput and that hookEventName
    equals "SessionStart" — a typo in the emitting hook's key name (e.g.
    "sessionTittle") must fail loudly here rather than silently no-op in
    production.

    home: when set, overrides $HOME in the subprocess environment so the
    hook writes into an isolated temp directory rather than real ~/.claude —
    required for kill-switch test cases, since without it a machine with the
    real sentinel present turns the whole test file vacuously green.
    extra_env: additional environment variables merged on top of the base env
    (applied after home override, so extra_env can also override HOME).
    """
    env = _build_subprocess_env(home, extra_env)
    result = subprocess.run(
        [str(hook)],
        input=json.dumps(tool_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        check=False,
    )
    if not result.stdout.strip():
        return None
    payload = json.loads(result.stdout)
    hook_specific_output = payload["hookSpecificOutput"]
    assert set(hook_specific_output.keys()) == {"hookEventName", "sessionTitle"}, (
        f"SessionStart hook emitted unexpected keys {sorted(hook_specific_output.keys())} "
        '— the harness routes on the exact {"hookEventName", "sessionTitle"} pair'
    )
    assert hook_specific_output["hookEventName"] == "SessionStart", (
        f'SessionStart hook emitted hookEventName={hook_specific_output["hookEventName"]!r}, '
        'expected "SessionStart"'
    )
    return hook_specific_output["sessionTitle"]


def posttooluse_input(file_path: str) -> dict:
    """Build a PostToolUse Write event payload for consume-migration-token tests.

    Covers the payload shape only — env setup is the caller's responsibility.
    Tests routing through run_hook / run_hook_reason must also pass
    extra_env={"CLAUDE_PLUGIN_ROOT": str(PLUGIN_ROOT)}; without it the hook
    exits 0 via fail-open before touching any token, making token-state
    assertions vacuously true. The consume test suite uses its own _run_consume
    runner to enforce this contract explicitly.
    """
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Write",
        "tool_input": {"file_path": file_path, "content": "x"},
    }


def bash_input(
    command: str,
    session_id: str | None = None,
    agent_type: str | None = None,
    cwd: str | None = None,
) -> dict:
    payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if session_id is not None:
        payload["session_id"] = session_id
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def edit_input(
    file_path: str,
    agent_type: str | None = None,
    cwd: str | None = None,
    old_string: str = "a",
    new_string: str = "b",
    replace_all: bool | None = None,
) -> dict:
    """`old_string`/`new_string`/`replace_all` default to the prior
    hardcoded placeholders ("a" -> "b", no replace_all field) — existing
    call sites that don't care about content keep the same payload. Pass
    them explicitly for a content-dependent test (e.g. a manifest-diffing
    hook)."""
    tool_input: dict = {"file_path": file_path, "old_string": old_string, "new_string": new_string}
    if replace_all is not None:
        tool_input["replace_all"] = replace_all
    payload: dict = {"tool_name": "Edit", "tool_input": tool_input}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def write_input(
    file_path: str,
    agent_type: str | None = None,
    cwd: str | None = None,
    content: str = "x",
) -> dict:
    """`content` defaults to the prior hardcoded placeholder — existing call
    sites that don't care about content keep the same payload."""
    payload: dict = {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def multiedit_input(
    file_path: str, agent_type: str | None = None, cwd: str | None = None, edits: list | None = None
) -> dict:
    """`edits` defaults to the prior hardcoded empty list — existing call
    sites that don't care about content keep the same payload. Each item is
    a dict with `old_string`/`new_string` and an optional `replace_all`,
    matching the real MultiEdit tool_input shape."""
    payload: dict = {
        "tool_name": "MultiEdit",
        "tool_input": {"file_path": file_path, "edits": edits if edits is not None else []},
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def stop_input(
    last_assistant_message: str,
    session_id: str | None = None,
    prompt_id: str | None = None,
    agent_type: str | None = None,
    permission_mode: str | None = None,
    cwd: str | None = None,
) -> dict:
    """Build a Stop event payload matching the real harness shape for
    advance-past-commit-stall.sh's tests."""
    payload: dict = {
        "hook_event_name": "Stop",
        "last_assistant_message": last_assistant_message,
    }
    if session_id is not None:
        payload["session_id"] = session_id
    if prompt_id is not None:
        payload["prompt_id"] = prompt_id
    if agent_type is not None:
        payload["agent_type"] = agent_type
    if permission_mode is not None:
        payload["permission_mode"] = permission_mode
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def exitplanmode_input(plan_file_path: str = "/nonexistent/.claude/plans/test-plan.md") -> dict:
    """Build an ExitPlanMode event payload matching the real harness shape.

    The ExitPlanMode tool_input has `plan` and `planFilePath` fields — no
    `file_path` field. The hook extracts `.tool_input.file_path // empty`,
    which yields an empty string for this payload, so the path-scope filter
    is skipped and the gate applies unconditionally.

    Field names (`plan`, `planFilePath` camelCase) verified empirically via
    live plan-mode session observation (spike run, prior session).
    """
    return {
        "tool_name": "ExitPlanMode",
        "tool_input": {
            "plan": "# Test plan\n\nTest plan content for spike/unit tests.",
            "planFilePath": plan_file_path,
        },
    }


def read_input(file_path: str, session_id: str | None = None) -> dict:
    payload: dict = {"tool_name": "Read", "tool_input": {"file_path": file_path}}
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


def agent_input(
    session_id: str | None = None,
    subagent_type: str | None = None,
    prompt: str | None = None,
    tool_name: str = "Agent",
    cwd: str | None = None,
    description: str = "test",
) -> dict:
    """Build an Agent (or Task) dispatch payload.

    `tool_name` defaults to "Agent" (the harness's confirmed subagent-dispatch
    tool name), overridable to "Task" for hooks registered on the Agent|Task
    matcher union (require-architect-consult.sh, log-reviewer-round.sh).
    `subagent_type` is omitted from tool_input when None, matching a
    dispatch with no reviewer-persona target. `prompt` defaults to the
    literal string "test" when None, preserving every pre-existing caller's
    payload shape. `description` defaults to `"test"` when not provided,
    preserving existing callers' payload shape. It is threaded through as a
    parameter so a `deny-no-op-dispatch.sh` fixture can construct a payload
    with a distinct description without a separate builder.
    """
    tool_input: dict = {"description": description, "prompt": prompt if prompt is not None else "test"}
    if subagent_type is not None:
        tool_input["subagent_type"] = subagent_type
    payload: dict = {"tool_name": tool_name, "tool_input": tool_input}
    if session_id is not None:
        payload["session_id"] = session_id
    if cwd is not None:
        payload["cwd"] = cwd
    return payload


def skill_input(
    skill_name: str, session_id: str | None = None, agent_type: str | None = None
) -> dict:
    """Build a Skill tool_use payload. `skill_name` lands under
    `tool_input.skill` -- the field name pinned by capturing a real `Skill`
    tool_use record from a local transcript (not documented in the harness's
    hooks/tools-reference pages)."""
    payload: dict = {"tool_name": "Skill", "tool_input": {"skill": skill_name}}
    if session_id is not None:
        payload["session_id"] = session_id
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


# activate-handoff-bypass.sh wraps its marker.sh call in a 2s `_lib_capped_for`
# cap that can be exceeded under parallel-test-worker contention -- see that
# hook's own header comment. Retrying is safe because `marker.sh activate` is
# idempotent (marker.sh:387-392 -- it just overwrites the same PID file).
ACTIVATE_MARKER_RETRY_ATTEMPTS = 10


def run_hook_until_marker_exists(
    hook: Path,
    tool_input: dict,
    marker: Path,
    attempts: int = ACTIVATE_MARKER_RETRY_ATTEMPTS,
    home: Path | None = None,
    extra_env: dict | None = None,
) -> None:
    """Retry `hook` against `tool_input` until `marker` exists, or fail."""
    for _ in range(attempts):
        run_hook(hook, tool_input, home=home, extra_env=extra_env)
        if marker.exists():
            return
        time.sleep(0.5)
    assert marker.exists(), (
        f"marker never landed at {marker} after {attempts} attempts -- "
        "not just a single cap-timeout miss"
    )


# -- Hostile session_id ------------------------------------------------------
#
# Every hook that builds a filesystem path from the payload's `.session_id`
# guards it with `_lib_valid_session_id_component` (_lib.sh). The tests that
# pin that guard all share one setup: put a file where a traversing id would
# resolve, run the hook with that id, and check the file survived. The three
# names below are that shared setup; the per-hook sink assertions are not
# shared, because each hook reaches the traversed path by a different sink
# (`rm -f`, `touch`, a truncating write, a `--checkpoint` argument) and the
# assertion that discriminates guard-present from guard-absent differs with it.

TRAVERSAL_SESSION_ID = "../canary"
"""A session_id that escapes one directory level when concatenated into
`$HOME/.claude/<marker-dir>/$SESSION_ID`, resolving to `$HOME/.claude/canary`.
Single level is deliberate: every marker directory this suite exercises sits
directly under `~/.claude`, so one `..` lands in a directory that exists and
the traversal is live rather than inert on a missing path component."""

CANARY_CONTENT = "untouched\n"


def plant_traversal_canary(home: Path, name: str = "canary") -> Path:
    """Create the file that `TRAVERSAL_SESSION_ID` resolves to, and return it.

    `name` covers hooks that build more than one path from the session id
    (e.g. a marker plus a `-drift` sidecar), each needing its own canary.
    """
    canary = home / ".claude" / name
    canary.write_text(CANARY_CONTENT)
    return canary


def assert_gate_handles_traversal_session_id(
    hook: Path,
    make_input,
    home: Path,
    expected_decision: str,
    cwd: Path | None = None,
) -> None:
    """Assert a PreToolUse gate's decision for a traversing session_id, and
    that it touched nothing outside its marker directory.

    `make_input` is a callable taking a session id and returning the hook
    payload, rather than a prebuilt payload: the helper supplies
    `TRAVERSAL_SESSION_ID` itself, so a caller cannot pass a payload carrying
    some other id and leave the test asserting nothing.

    `expected_decision` is per-hook and not a constant. Bypass-shaped gates
    (the marker grants an exception to a standing deny) must withhold the
    exception and deny; activation-shaped gates (the marker turns enforcement
    on) must leave it off and allow. Callers state which they are.
    """
    payload = make_input(TRAVERSAL_SESSION_ID)
    # A make_input that drops the id it was handed would leave this test
    # asserting a hook's disposition for an ordinary payload — passing, and
    # pinning nothing about traversal. Checking the built payload closes that,
    # since supplying the id is the only reason the callable form exists.
    assert TRAVERSAL_SESSION_ID in json.dumps(payload), (
        f"make_input did not thread the session id into the payload: {payload!r}"
    )
    canary = plant_traversal_canary(home)
    assert run_hook(hook, payload, cwd=cwd) == expected_decision
    assert canary.read_text() == CANARY_CONTENT, (
        "a traversal session_id must not touch a file outside the marker dir"
    )


def git_toplevel(repo: Path) -> str:
    """Return what `git rev-parse --show-toplevel` sees — this is what the
    hook hashes, and it may differ from `str(repo)` when /tmp is a symlink."""
    return subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


DEFAULT_TEST_SESSION_ID = "test-session-default"


def marker_path(
    home: Path,
    repo: Path,
    session_id: str = DEFAULT_TEST_SESSION_ID,
    config_dir: Path | None = None,
) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    config_dir = config_dir if config_dir is not None else home / ".claude"
    return config_dir / "code-review-markers" / f"{repo_hash}.{session_id}"


def staged_diff_hash(repo: Path) -> str:
    """The plain HEAD-relative preimage: `git diff --cached` with no base
    override. This is today's production recipe, and stays exactly this
    outside any in-progress git state (merge/rebase/cherry-pick/revert),
    where _lib_gate_diff_base resolves no base and the real hooks issue
    this identical command. See staged_diff_hash_at_base() below for the
    base-relative oracle used inside a trusted in-progress state -- this
    function is deliberately not extended to take a base, since the
    marker-invalidation tests need exactly this old recipe to build an
    old-preimage marker."""
    diff = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def staged_diff_hash_at_base(repo: Path, base: str, *pathspecs: str) -> str:
    """Independent oracle for a base-relative marker preimage: computes
    `git diff --cached [<base>] [-- PATHSPEC...]` directly in Python rather
    than by calling the production `_lib_staged_diff_hash`/`_lib_gate_diff_base`
    shell functions under test -- a test seeding a marker via the function it is
    testing would only prove the function agrees with itself, not that its
    output is correct (see write_plan_review_marker's docstring below for
    the same caution applied to a different marker kind). An empty `base`
    omits the base argument entirely rather than passing it to git as an
    empty string, so one call expresses both recipes:
    staged_diff_hash_at_base(repo, "", *pathspecs) is the old HEAD-relative
    preimage, scoped to PATHSPEC when given."""
    args = ["git", "diff", "--cached"]
    if base:
        args.append(base)
    if pathspecs:
        args.append("--")
        args.extend(pathspecs)
    diff = subprocess.run(args, cwd=repo, capture_output=True, check=True).stdout
    return hashlib.sha256(diff).hexdigest()


def _run_git(repo: Path, *args: str) -> str:
    """Run a git subcommand in `repo`, returning stdout. Raises on failure --
    for the failure-is-expected calls in the in-progress-state builders
    below (a merge/rebase/cherry-pick/revert whose whole point is to
    conflict), use subprocess.run directly and assert on the returncode."""
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, check=True
    ).stdout


def _current_branch(repo: Path) -> str:
    return _run_git(repo, "symbolic-ref", "--short", "HEAD").strip()


def absolute_git_dir(repo: Path) -> Path:
    """Resolve `repo`'s real gitdir via `git rev-parse --absolute-git-dir`,
    rather than assuming `repo / ".git"` is a directory -- a linked worktree
    makes `.git` a file pointing elsewhere, and the in-progress-state marker
    files (MERGE_HEAD, REVERT_HEAD, CHERRY_PICK_HEAD, rebase-merge/) live in
    that real gitdir, not under the worktree's own `.git`."""
    return Path(_run_git(repo, "rev-parse", "--absolute-git-dir").strip())


def _seed_tracked_file(repo: Path, file_name: str, content: str = "base\n") -> Path:
    """Ensure `file_name` exists, tracked, and committed in `repo`, so a
    conflict-building fixture has a common baseline both diverging sides
    can edit differently -- an add/add conflict (both sides create the file
    independently) exercises different git machinery than the
    content-conflict shape these fixtures need. Idempotent: does nothing if
    the file is already tracked."""
    target = repo / file_name
    if not target.exists():
        target.write_text(content)
        _run_git(repo, "add", file_name)
        _run_git(repo, "commit", "-qm", f"seed {file_name}")
    return target


def bare_remote_with_default_branch(
    tmp_path: Path,
    branch: str = "main",
    file_name: str = "f",
    file_content: str = "a\n",
) -> tuple[Path, Path]:
    """Build a bare 'origin' repo and a clone checked out on `branch`, with
    origin/HEAD set and one shared commit -- the bare-remote-plus-clone
    shape test_check_branch_divergence.py's bare_remote/feature_clone
    pytest fixtures already establish, generalized to a plain function so
    every test file needing a pushable remote (the trusted/untrusted-anchor
    tests here, and require-ready-for-review.sh's push-anchor test) can
    call it directly rather than duplicating the construction. Returns
    (bare_remote, clone)."""
    bare = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", branch, str(bare)], check=True, capture_output=True
    )
    seed = tmp_path / "_bare_remote_seed"
    seed.mkdir()
    subprocess.run(["git", "init", "-q", "-b", branch, str(seed)], check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=seed, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=seed, check=True)
    (seed / file_name).write_text(file_content)
    subprocess.run(["git", "add", file_name], cwd=seed, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=seed, check=True)
    subprocess.run(["git", "remote", "add", "origin", str(bare)], cwd=seed, check=True)
    subprocess.run(["git", "push", "-q", "origin", branch], cwd=seed, check=True)

    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=clone, check=True)
    subprocess.run(["git", "remote", "set-head", "origin", branch], cwd=clone, check=True)
    return bare, clone


def push_conflicting_edit_to_origin(
    tmp_path: Path, bare: Path, file_name: str, content: str, branch: str = "main"
) -> None:
    """Push a new commit editing `file_name` to `bare`'s default branch from
    a throwaway clone, independent of any other clone's own worktree -- the
    "someone else pushed while I was working" shape a real sync merge
    needs, and the shape test_check_branch_divergence.py's
    repo_behind_conflict fixture already establishes. Does not touch any
    other clone; callers run `git fetch origin` there afterward to see the
    new origin/<branch> tip."""
    push_clone = tmp_path / f"_push_{branch}_{abs(hash(content))}"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    (push_clone / file_name).write_text(content)
    subprocess.run(["git", "add", file_name], cwd=push_clone, check=True)
    subprocess.run(
        ["git", "commit", "-qm", f"origin edits {file_name}"], cwd=push_clone, check=True
    )
    subprocess.run(["git", "push", "-q", "origin", branch], cwd=push_clone, check=True)


def build_conflicted_merge_via_origin_with_upstream_skill_edit(
    tmp_path: Path,
    *,
    skill_name: str = "example-skill",
    conflict_file: str = "f",
    upstream_adds_skill: bool = False,
) -> Path:
    """Merge fixture with a nested claude-skills/skills/<skill_name>/SKILL.md
    edited only upstream, before the merge, alongside an edit to
    `conflict_file` in the same upstream commit -- it auto-merges into the
    local worktree unchanged, so SKILL.md reads as active relative to plain
    HEAD (upstream's whole contribution) but not relative to the trusted
    merge-tree base (already-reviewed content excluded). The conflict is
    engineered in `conflict_file`, unrelated to SKILL.md, so MERGE_HEAD
    persists to a resolvable state. With `upstream_adds_skill`, SKILL.md does
    not exist before the fork and upstream's commit adds it instead of
    editing it. Mirrors
    _build_conflicted_merge_via_origin_with_upstream_plan_edit in
    test_marker_script.py for the plan-review marker kind, generalized to a
    shared helper since the skill-review gate's tests need the same shape.
    Neither bare_remote_with_default_branch nor push_conflicting_edit_to_origin
    creates parent directories, so this builder does its own mkdir -p for the
    nested skill path."""
    bare, clone = bare_remote_with_default_branch(tmp_path)
    skill_rel_path = f"claude-skills/skills/{skill_name}/SKILL.md"
    if not upstream_adds_skill:
        skill_path = clone / skill_rel_path
        skill_path.parent.mkdir(parents=True)
        skill_path.write_text("base skill\n")
        subprocess.run(["git", "add", skill_rel_path], cwd=clone, check=True)
        subprocess.run(["git", "commit", "-qm", "seed SKILL.md"], cwd=clone, check=True)
        subprocess.run(["git", "push", "-q", "origin", "main"], cwd=clone, check=True)

    (clone / conflict_file).write_text("ours-edit\n")
    subprocess.run(["git", "add", conflict_file], cwd=clone, check=True)
    subprocess.run(["git", "commit", "-qm", f"ours edits {conflict_file}"], cwd=clone, check=True)

    push_clone = tmp_path / f"_push_upstream_skill_edit_{skill_name}"
    subprocess.run(
        ["git", "clone", "-q", str(bare), str(push_clone)], check=True, capture_output=True
    )
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=push_clone, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=push_clone, check=True)
    (push_clone / conflict_file).write_text("origin-edit\n")
    (push_clone / skill_rel_path).parent.mkdir(parents=True, exist_ok=True)
    (push_clone / skill_rel_path).write_text("upstream edited skill\n")
    subprocess.run(["git", "add", conflict_file, skill_rel_path], cwd=push_clone, check=True)
    subprocess.run(
        ["git", "commit", "-qm", f"origin edits {conflict_file} and SKILL.md"],
        cwd=push_clone,
        check=True,
    )
    subprocess.run(["git", "push", "-q", "origin", "main"], cwd=push_clone, check=True)

    subprocess.run(["git", "fetch", "-q", "origin"], cwd=clone, check=True)
    result = subprocess.run(
        ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert (absolute_git_dir(clone) / "MERGE_HEAD").exists()
    (clone / conflict_file).write_text("resolved\n")
    subprocess.run(["git", "add", conflict_file], cwd=clone, check=True)
    return clone


def build_conflicted_merge(repo: Path, *, file_name: str = "f") -> str:
    """Build a real conflicted two-way merge inside `repo`: branch "theirs"
    off the checked-out branch, edit `file_name` differently on each side,
    then `git merge theirs` on the original branch. Leaves MERGE_HEAD and
    unresolved conflict markers staged. `repo` must already have a
    configured user.email/user.name. Returns the merged-in branch's tip
    oid -- MERGE_HEAD's expected content."""
    base_branch = _current_branch(repo)
    target = _seed_tracked_file(repo, file_name)
    _run_git(repo, "checkout", "-qb", "theirs")
    target.write_text("theirs-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"theirs edits {file_name}")
    theirs_oid = _run_git(repo, "rev-parse", "HEAD").strip()
    _run_git(repo, "checkout", "-q", base_branch)
    target.write_text("ours-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"ours edits {file_name}")
    result = subprocess.run(
        ["git", "merge", "-q", "theirs"], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode != 0, (
        f"expected merge conflict, got: {result.stdout}{result.stderr}"
    )
    assert (absolute_git_dir(repo) / "MERGE_HEAD").exists(), "merge did not leave MERGE_HEAD"
    return theirs_oid


def build_conflicted_cherry_pick(repo: Path, *, file_name: str = "f") -> str:
    """Build a real conflicted cherry-pick inside `repo`: branch "source"
    off the checked-out branch, commit a conflicting edit to `file_name` on
    each side, then `git cherry-pick` the source commit back onto the
    original branch. Leaves CHERRY_PICK_HEAD and unresolved conflict
    markers staged. Returns the cherry-picked commit's oid -- CHERRY_PICK_HEAD's
    expected content."""
    base_branch = _current_branch(repo)
    target = _seed_tracked_file(repo, file_name)
    _run_git(repo, "checkout", "-qb", "source")
    target.write_text("source-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"source edits {file_name}")
    source_oid = _run_git(repo, "rev-parse", "HEAD").strip()
    _run_git(repo, "checkout", "-q", base_branch)
    target.write_text("base-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"base edits {file_name}")
    result = subprocess.run(
        ["git", "cherry-pick", source_oid], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode != 0, (
        f"expected cherry-pick conflict, got: {result.stdout}{result.stderr}"
    )
    assert (absolute_git_dir(repo) / "CHERRY_PICK_HEAD").exists(), "cherry-pick did not leave CHERRY_PICK_HEAD"
    return source_oid


def build_conflicted_revert(repo: Path, *, file_name: str = "f") -> str:
    """Build a real conflicted revert inside `repo`: commit A introduces an
    edit to `file_name`, commit B further edits the same region, then
    `git revert A`. Reverting the tip essentially never conflicts, so this
    three-commit shape is required to exercise the conflicting case. Leaves
    REVERT_HEAD and unresolved conflict markers staged. Returns commit A's
    oid -- REVERT_HEAD's expected content."""
    target = _seed_tracked_file(repo, file_name)
    target.write_text("A-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", "commit A")
    commit_a = _run_git(repo, "rev-parse", "HEAD").strip()
    target.write_text("B-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", "commit B")
    result = subprocess.run(
        ["git", "revert", "--no-edit", commit_a], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode != 0, (
        f"expected revert conflict, got: {result.stdout}{result.stderr}"
    )
    assert (absolute_git_dir(repo) / "REVERT_HEAD").exists(), "revert did not leave REVERT_HEAD"
    return commit_a


def build_conflicted_revert_with_clean_gated_removal(
    repo: Path, *, skill_name: str = "revert-skill", conflict_file: str = "f"
) -> str:
    """Build a real conflicted revert whose gated-SKILL.md removal applies
    cleanly: commit X edits claude-skills/skills/<skill_name>/SKILL.md and
    `conflict_file`, commit Y edits `conflict_file` again, then `git revert
    X` conflicts in `conflict_file` only. The SKILL.md revert is staged
    without conflict and `conflict_file` is resolved, so the index holds a
    gated removal that differs from HEAD but equals the revert's own
    synthesized subtraction tree. A gate wrongly diffing against that
    tree sees an empty gated diff; one diffing against HEAD does not.
    Returns commit X's oid -- REVERT_HEAD's expected content."""
    conflict_target = _seed_tracked_file(repo, conflict_file)
    skill_rel_path = f"claude-skills/skills/{skill_name}/SKILL.md"
    skill_path = repo / skill_rel_path
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("original skill\n")
    _run_git(repo, "add", skill_rel_path)
    _run_git(repo, "commit", "-qm", "seed SKILL.md")
    skill_path.write_text("edited skill\n")
    conflict_target.write_text("X-edit\n")
    _run_git(repo, "add", skill_rel_path, conflict_file)
    _run_git(repo, "commit", "-qm", "commit X")
    commit_x = _run_git(repo, "rev-parse", "HEAD").strip()
    conflict_target.write_text("Y-edit\n")
    _run_git(repo, "add", conflict_file)
    _run_git(repo, "commit", "-qm", "commit Y")
    result = subprocess.run(
        ["git", "revert", "--no-edit", commit_x], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode != 0, (
        f"expected revert conflict, got: {result.stdout}{result.stderr}"
    )
    assert (absolute_git_dir(repo) / "REVERT_HEAD").exists(), "revert did not leave REVERT_HEAD"
    unmerged = _run_git(repo, "diff", "--name-only", "--diff-filter=U").split()
    assert unmerged == [conflict_file], f"conflict must be confined to {conflict_file}: {unmerged}"
    conflict_target.write_text("resolved\n")
    _run_git(repo, "add", conflict_file)
    return commit_x


def revert_subtraction_base(repo: Path) -> str:
    """Independently computes the tree `_lib_gate_diff_base` returns
    mid-revert: `git merge-tree --write-tree --merge-base=<REVERT_HEAD>
    HEAD <REVERT_HEAD>^`, with the literal OID (not a ref name) so the
    conflict-marker labels match production's."""
    revert_head_oid = (absolute_git_dir(repo) / "REVERT_HEAD").read_text().strip()
    out = subprocess.run(
        ["git", "merge-tree", "--write-tree", f"--merge-base={revert_head_oid}",
         "HEAD", f"{revert_head_oid}^"],
        cwd=repo, capture_output=True, text=True, check=False,
    ).stdout
    return out.strip().splitlines()[0]


def build_conflicted_rebase(
    repo: Path, *, file_name: str = "f", interactive: bool = False
) -> str:
    """Build a real conflicted rebase inside `repo`, non-interactive by
    default: branch "upstream" off the checked-out branch, edit
    `file_name` differently on each side, then rebase the original branch
    onto "upstream". Returns control at the pre-resolution checkpoint --
    the index carries genuine stage 1/2/3 entries for `file_name` and the
    conflict is not yet resolved -- with the replayed commit's oid
    (REBASE_HEAD's expected content). Call resolve_conflicted_rebase() to
    advance to the post-resolution, staged checkpoint.

    `interactive=True` runs `git -c sequence.editor=true rebase -i
    upstream` instead: an interactive rebase whose todo list is accepted
    unmodified. Unlike the plain form, git implements each interactive
    "pick" step via the same code path as cherry-pick, so this leaves a
    CHERRY_PICK_HEAD alongside rebase-merge/ while paused -- the fixture
    the state-detection precedence order (rebase before cherry-pick) is
    verified against."""
    base_branch = _current_branch(repo)
    target = _seed_tracked_file(repo, file_name)
    _run_git(repo, "checkout", "-qb", "upstream")
    target.write_text("upstream-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"upstream edits {file_name}")
    _run_git(repo, "checkout", "-q", base_branch)
    target.write_text("feature-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"feature edits {file_name}")
    feature_tip = _run_git(repo, "rev-parse", "HEAD").strip()

    cmd = (
        ["git", "-c", "sequence.editor=true", "rebase", "-i", "upstream"]
        if interactive
        else ["git", "rebase", "upstream"]
    )
    result = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    assert result.returncode != 0, (
        f"expected rebase conflict, got: {result.stdout}{result.stderr}"
    )
    gitdir = absolute_git_dir(repo)
    assert (gitdir / "rebase-merge").is_dir() or (gitdir / "rebase-apply").is_dir(), (
        "rebase did not leave rebase-merge/ or rebase-apply/"
    )
    # Whether the installed git writes REBASE_HEAD is asserted, not assumed
    # -- git's older apply-based backend (rebase-apply/) predates
    # REBASE_HEAD, so this is only guaranteed on the merge-based backend
    # (the default since git 2.26) that both the plain and interactive
    # forms exercised here use.
    assert (gitdir / "REBASE_HEAD").exists(), "rebase did not leave REBASE_HEAD"
    return feature_tip


def resolve_conflicted_rebase(
    repo: Path, *, file_name: str = "f", resolution: str = "resolved\n"
) -> None:
    """Advance a build_conflicted_rebase() fixture to the post-resolution,
    staged checkpoint: write `resolution` to `file_name` and `git add` it,
    leaving the rebase paused with a clean, staged resolution ready for
    `git rebase --continue`."""
    (repo / file_name).write_text(resolution)
    _run_git(repo, "add", file_name)


def build_octopus_merge_conflict(repo: Path, *, file_name: str = "f") -> None:
    """Forge a genuine multi-line MERGE_HEAD naming two real, divergent
    commits. git's own octopus merge strategy aborts outright on any
    conflicting step rather than leaving a resolvable state to fix by
    hand -- confirmed empirically: `git merge b1 b2` against branches
    diverged from a shared base and conflicting on the same file exits
    nonzero with no MERGE_HEAD left at all, unlike the two-line MERGE_HEAD
    plus ordinary conflict markers a two-parent merge leaves. Producing
    this on-disk shape -- which the OID-validation code must still handle
    defensively -- means writing MERGE_HEAD directly, naming two real
    commits so the ancestry check this fixture exercises runs against
    genuine objects rather than fabricated ones."""
    base_branch = _current_branch(repo)
    target = _seed_tracked_file(repo, file_name)
    base_sha = _run_git(repo, "rev-parse", "HEAD").strip()
    oids = []
    for branch, content in (("b1", "b1-edit\n"), ("b2", "b2-edit\n")):
        _run_git(repo, "checkout", "-qb", branch, base_sha)
        target.write_text(content)
        _run_git(repo, "add", file_name)
        _run_git(repo, "commit", "-qm", f"{branch} edits {file_name}")
        oids.append(_run_git(repo, "rev-parse", "HEAD").strip())
    _run_git(repo, "checkout", "-q", base_branch)
    (repo / ".git" / "MERGE_HEAD").write_text("\n".join(oids) + "\n")


def build_rebase_merges_replay_conflict(repo: Path, *, file_name: str = "f") -> None:
    """Build a `git rebase --rebase-merges upstream` replay that conflicts
    while reconstructing a merge commit, so REBASE_HEAD names that merge
    commit. This is the topology where REBASE_HEAD^ would silently resolve
    to parent 1 rather than erroring.

    `upstream` edits `file_name`. `side` and `feature` both edit a second
    file, `second_file_name`, differently, so each replays cleanly onto
    `upstream` individually -- `upstream` never touches that file. The
    conflict surfaces only when --rebase-merges reconstructs the merge step
    combining `side` and `feature`: this fixture replays, by hand, the same
    conflict `feature`'s own merge of `side` hit originally. A conflict
    placed directly in `file_name` instead would surface during an earlier
    individual pick step and never reach the merge reconstruction at all --
    confirmed empirically."""
    second_file_name = f"{file_name}2"
    target = _seed_tracked_file(repo, file_name)
    second_target = repo / second_file_name
    second_target.write_text("base\n")
    _run_git(repo, "add", second_file_name)
    _run_git(repo, "commit", "-qm", f"seed {second_file_name}")
    base_sha = _run_git(repo, "rev-parse", "HEAD").strip()

    _run_git(repo, "checkout", "-qb", "upstream", base_sha)
    target.write_text("upstream-edit\n")
    _run_git(repo, "add", file_name)
    _run_git(repo, "commit", "-qm", f"upstream edits {file_name}")

    _run_git(repo, "checkout", "-qb", "side", base_sha)
    second_target.write_text("side-edit\n")
    _run_git(repo, "add", second_file_name)
    _run_git(repo, "commit", "-qm", f"side edits {second_file_name}")

    _run_git(repo, "checkout", "-qb", "feature", base_sha)
    second_target.write_text("feature-edit\n")
    _run_git(repo, "add", second_file_name)
    _run_git(repo, "commit", "-qm", f"feature edits {second_file_name}")
    merge_result = subprocess.run(
        ["git", "merge", "--no-ff", "-q", "side"], cwd=repo, capture_output=True, text=True
    )
    assert merge_result.returncode != 0, (
        f"expected feature's own merge of side to conflict, got: "
        f"{merge_result.stdout}{merge_result.stderr}"
    )
    second_target.write_text("resolved\n")
    _run_git(repo, "add", second_file_name)
    subprocess.run(
        ["git", "commit", "--no-edit", "-q"],
        cwd=repo, check=True, capture_output=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    merge_oid = _run_git(repo, "rev-parse", "HEAD").strip()

    result = subprocess.run(
        ["git", "rebase", "--rebase-merges", "upstream"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode != 0, (
        f"expected rebase --rebase-merges conflict, got: {result.stdout}{result.stderr}"
    )
    assert (repo / ".git" / "rebase-merge").is_dir(), "rebase did not leave rebase-merge/"
    rebase_head = _run_git(repo, "rev-parse", "REBASE_HEAD").strip()
    assert rebase_head == merge_oid, (
        "expected REBASE_HEAD to name the original merge commit, not an "
        "individually-replayed side commit"
    )
    parents = _run_git(repo, "rev-list", "--parents", "-n", "1", rebase_head).split()
    assert len(parents) >= 3, "expected REBASE_HEAD to be a merge commit with 2+ parents"


def write_marker(
    home: Path,
    repo: Path,
    diff_hash: str,
    session_id: str = DEFAULT_TEST_SESSION_ID,
    config_dir: Path | None = None,
) -> Path:
    marker = marker_path(home, repo, session_id, config_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(diff_hash + "\n")
    return marker


def head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def stage_settings(repo: Path, settings_file: Path, content: str) -> None:
    """Write `content` to `settings_file` and stage it."""
    settings_file.write_text(content)
    subprocess.run(
        ["git", "add", "claude/.claude/settings.json"],
        cwd=repo, check=True,
    )


def plan_review_marker_path(
    home: Path, repo: Path, session_id: str, config_dir: Path | None = None
) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    config_dir = config_dir if config_dir is not None else home / ".claude"
    return config_dir / "plan-review-markers" / f"{repo_hash}.{session_id}"


def write_plan_review_marker(
    home: Path, repo: Path, session_id: str, config_dir: Path | None = None, base: str = ""
) -> Path:
    """Write a plan-review completion marker whose content is the real
    active-plan hash for `repo`, computed by shelling out to the production
    `_lib_active_plan_hash` (_lib.sh) rather than reimplementing the recipe
    in Python, which would diverge silently on any
    newline/delimiter/normalization detail. `base` defaults to "" (no
    trusted in-progress state) -- pass the merge-tree base explicitly for a
    marker meant to validate mid-merge/rebase/cherry-pick/revert.

    Note the tradeoff, and do not mistake this for the technique
    `write_marker` uses: its callers recompute the hash independently in
    Python from a real `git diff` (see `staged_diff_hash`), so they can catch
    drift in the shell-side recipe. This one calls the very function under
    test, so a test that seeds a marker here and asserts the hook allows is
    checking that the function agrees with itself across two invocations --
    not that its output is correct. Independent correctness is covered by
    the relational unit tests in `hooks/tests/test_marker_lib.py`, which do
    not route through this helper. `write_skill_review_marker` takes the
    same tradeoff, for the pathspec-list-drift reason its own docstring
    states."""
    marker = plan_review_marker_path(home, repo, session_id, config_dir)
    marker.parent.mkdir(parents=True, exist_ok=True)
    lib_sh = HOOKS_DIR / "_lib.sh"
    active_plan_hash = subprocess.run(
        ["bash", "-c", f'. "{lib_sh}"; _lib_active_plan_hash "$1" "$2"',
         "write_plan_review_marker", str(repo), base],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    marker.write_text(active_plan_hash)
    return marker


def skill_review_marker_path(
    home: Path,
    repo: Path,
    session_id: str = DEFAULT_TEST_SESSION_ID,
    config_dir: Path | None = None,
) -> Path:
    repo_hash = subprocess.run(
        ["sha256sum"],
        input=git_toplevel(repo).encode(),
        capture_output=True,
    ).stdout.decode().split()[0]
    config_dir = config_dir if config_dir is not None else home / ".claude"
    return config_dir / "skill-review-markers" / f"{repo_hash}.{session_id}"


def write_skill_review_marker(
    home: Path,
    repo: Path,
    session_id: str = DEFAULT_TEST_SESSION_ID,
    config_dir: Path | None = None,
) -> None:
    """Write a skill-review completion marker by shelling out to the real
    `marker.sh write skill-review` recipe, rather than hand-maintaining a
    second copy of the SKILL.md pathspec list here — marker.sh's own
    SKILL_REVIEW_PATHSPECS is the single source of truth.

    marker.sh resolves its session id from the caller's own process
    ancestry, so this seeds a $HOME/.claude/sessions/<pid> entry for the
    current test process first. Duplicates hooks/tests/conftest.py's
    _seed_session rather than importing it — that conftest is a pytest
    fixture file, not importable from this unpackaged test-support module.
    """
    pid = os.getpid()
    config_dir_resolved = config_dir if config_dir is not None else home / ".claude"
    sessions_dir = config_dir_resolved / "sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)
    start_time = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(pid)],
        env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
        capture_output=True,
        text=True,
        check=True,
    ).stdout.rstrip("\n")
    (sessions_dir / str(pid)).write_text(f"{session_id}\n{start_time}\n")

    extra_env = {"CLAUDE_CONFIG_DIR": str(config_dir)} if config_dir is not None else None
    subprocess.run(
        ["bash", str(SCRIPTS_DIR / "marker.sh"), "write", "skill-review"],
        cwd=repo,
        env=_build_subprocess_env(home, extra_env),
        capture_output=True,
        text=True,
        check=True,
    )


def plan_review_active_marker_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".plan-review-active.d" / session_id


def write_plan_review_active_marker(home: Path, session_id: str) -> Path:
    """Create a plan-review active marker with empty content.

    This produces a dead-PID marker intentionally for hooks that check marker
    existence only (e.g., require-routing-read.sh, log-routing-read.sh).
    require-plan-review.sh reads the file and validates the PID with kill -0,
    so an empty-content marker is immediately evicted by that hook. For tests
    that need a live-marker bypass in require-plan-review.sh, write the PID
    directly: `(marker_dir / sid).write_text(str(os.getpid()))`.
    """
    marker = plan_review_active_marker_path(home, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return marker


def plan_review_routing_read_marker_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".plan-review-routing-read.d" / session_id


def write_plan_review_routing_read_marker(home: Path, session_id: str) -> Path:
    marker = plan_review_routing_read_marker_path(home, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return marker


def plan_review_pending_read_marker_path(home: Path, session_id: str) -> Path:
    return home / ".claude" / ".plan-review-pending-read.d" / session_id


def reviewer_round_state_key(repo: Path) -> str:
    """Shell out to the real _lib_reviewer_round_state_key against `repo`,
    so a test's seeded state file lands at the exact path
    require-architect-consult.sh/log-reviewer-round.sh will look under.

    Uses git_toplevel(repo), not str(repo): the hooks resolve REPO_ROOT via
    `git -C "$CWD" rev-parse --show-toplevel` before hashing it, which
    normalizes a symlinked tmp prefix (e.g. macOS /tmp -> /private/tmp) that
    the raw tmp_path string would not — passing the unnormalized path here
    would key a test's seeded file under a different repo-hash than the
    hook computes at runtime.

    Returns "" (not raising) when the repo has no branch to key on (e.g.
    detached HEAD), mirroring the function's own fail-open contract.
    """
    result = subprocess.run(
        ["bash", "-c", f'. "{HOOKS_DIR}/_lib.sh"; _lib_reviewer_round_state_key "$1"',
         "_", git_toplevel(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def reviewer_round_state_value(repo: Path) -> str:
    """Shell out to the real _lib_reviewer_round_state_value against `repo`
    — see reviewer_round_state_key's docstring for the git_toplevel
    normalization rationale, which applies identically here. Returns ""
    (not raising) when HEAD is unresolvable (no commits yet), or when the
    staged-diff git call itself failed or was capped-killed."""
    result = subprocess.run(
        ["bash", "-c", f'. "{HOOKS_DIR}/_lib.sh"; _lib_reviewer_round_state_value "$1"',
         "_", git_toplevel(repo)],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout.strip()


def reviewer_round_state_path(config_dir: Path, repo: Path) -> Path:
    return config_dir / ".reviewer-round-state.d" / reviewer_round_state_key(repo)


def architect_consult_latch_path(config_dir: Path, repo: Path) -> Path:
    return config_dir / ".architect-consult-latch.d" / reviewer_round_state_key(repo)


def write_reviewer_round_state(config_dir: Path, repo: Path, values: list[str]) -> Path:
    """Seed the round-state file directly with `values` (each already a
    "<head-sha> <staged-diff-sha256>" line), bypassing log-reviewer-round.sh
    entirely — for tests that need a precondition (e.g. "at cap") set up
    without exercising the recorder itself."""
    state_file = reviewer_round_state_path(config_dir, repo)
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text("".join(f"{v}\n" for v in values))
    return state_file


def _symlink_if_absent(link: Path, target: Path) -> Path:
    """Create link -> target if link doesn't already exist. Idempotent."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if not link.exists():
        link.symlink_to(target)
    return link


def install_resume_context_script(isolated_home: Path) -> Path:
    """Symlink the real resume-context.sh into an isolated $HOME/.claude/scripts/.

    So hook/script tests that shell out to resume-context.sh (directly, or via
    consume-durable-continuity-file-on-read.sh) exercise the real script
    rather than a copy that can drift from it.
    """
    return _symlink_if_absent(
        isolated_home / ".claude" / "scripts" / "resume-context.sh",
        SCRIPTS_DIR / "resume-context.sh",
    )


def symlink_hooks_lib_chain(hooks_dir: Path) -> None:
    """Symlink _lib.sh and its two BASH_SOURCE-relative siblings,
    _config.sh and config-keys.psv, into an arbitrary hooks_dir -- the
    single source of truth for this three-symlink chain, called by every
    fixture/helper that needs a hooks/ dir where sourcing _lib.sh works.

    _lib.sh sources _config.sh from its own directory; BASH_SOURCE does
    not follow a symlink, so it resolves relative to the symlink's own
    location, not _lib.sh's real target. config-keys.psv is a required
    sibling of _config.sh for the same reason: _config.sh reads it via a
    BASH_SOURCE-relative path too. Idempotent, so a caller that already
    symlinked hooks/_lib.sh itself can call this unconditionally.
    """
    _symlink_if_absent(hooks_dir / "_lib.sh", HOOKS_DIR / "_lib.sh")
    _symlink_if_absent(hooks_dir / "_config.sh", HOOKS_DIR / "_config.sh")
    _symlink_if_absent(hooks_dir / "config-keys.psv", HOOKS_DIR / "config-keys.psv")


def install_marker_script(isolated_home: Path) -> Path:
    """Symlink the real marker.sh, and the hooks/_lib.sh chain it sources,
    into an isolated $HOME/.claude/ -- so a hook or skill recipe invoking
    marker.sh via `$CONFIG_DIR/scripts/marker.sh` resolves the real script
    rather than a missing one. Idempotent, so a caller under the
    `isolated_home` fixture (which already symlinks hooks/_lib.sh itself)
    can call this unconditionally.
    """
    symlink_hooks_lib_chain(isolated_home / ".claude" / "hooks")
    return _symlink_if_absent(
        isolated_home / ".claude" / "scripts" / "marker.sh", SCRIPTS_DIR / "marker.sh"
    )


def run_skill_command(command: str, cwd: Path, isolated_home: Path) -> None:
    """Run a SKILL.md-extracted bash command in a sandboxed $HOME."""
    install_marker_script(isolated_home)
    _symlink_if_absent(
        isolated_home / ".claude" / "scripts" / "ensure-account-dir.sh",
        SCRIPTS_DIR / "ensure-account-dir.sh",
    )
    subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=_build_subprocess_env(isolated_home, None),
        check=True,
    )


def extract_ci_detect_step_run_block() -> str:
    """Pull the `run:` script for tests.yml's step with `id: detect` via
    pyyaml, locating the step by id rather than regexing the YAML."""
    workflow = yaml.safe_load(_CI_DETECT_STEP_WORKFLOW.read_text())
    steps = workflow["jobs"]["tests"]["steps"]
    detect_steps = [step for step in steps if step.get("id") == "detect"]
    assert len(detect_steps) == 1, (
        "Expected exactly one step with id: detect in tests.yml — did "
        "the step move or get renamed?"
    )
    return detect_steps[0]["run"]


def substitute_ci_detect_step_expressions(script: str, base_sha: str, head_sha: str) -> str:
    """Replace GitHub Actions `${{ }}` expressions with literal values.

    This textual substitution is the one unavoidable fidelity gap in this
    test: the real Actions runner evaluates these expressions with its own
    expression engine before handing bash the resulting script, and that
    evaluator isn't available here, so plain string substitution stands in
    for it. Forcing github.event_name to a non-"pull_request" value routes
    through the push-event branch, which is the one that reads
    github.event.before / github.sha into BASE/HEAD.
    """
    substitutions = {
        "${{ github.event_name }}": "push",
        "${{ github.event.pull_request.base.sha }}": "unused-in-push-branch",
        "${{ github.event.pull_request.head.sha }}": "unused-in-push-branch",
        "${{ github.event.before }}": base_sha,
        "${{ github.sha }}": head_sha,
    }
    for placeholder, value in substitutions.items():
        script = script.replace(placeholder, value)
    return script


def init_ci_detect_step_test_repo(
    tmp_path: Path, second_commit_files: dict[str, str]
) -> tuple[Path, str, str]:
    """Build a throwaway two-commit git repo; return (repo, base_sha, head_sha)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=repo, check=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    for rel_path, content in second_commit_files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "second"], cwd=repo, check=True)
    head_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    return repo, base_sha, head_sha


def run_ci_detect_step(repo: Path, base_sha: str, head_sha: str) -> dict[str, str]:
    """Run the substituted detect script under bash; parse GITHUB_OUTPUT."""
    script = substitute_ci_detect_step_expressions(
        extract_ci_detect_step_run_block(), base_sha, head_sha
    )
    github_output = repo / "github_output.txt"
    github_output.write_text("")
    env = {**os.environ, "GITHUB_OUTPUT": str(github_output)}
    result = subprocess.run(
        ["bash", "-c", script], cwd=repo, env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, (
        f"detect step exited nonzero: {result.stdout}\n{result.stderr}"
    )
    outputs: dict[str, str] = {}
    for line in github_output.read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            outputs[key] = value
    return outputs


def _make_git_exiting_with_status(bin_dir: Path, arg_pattern: str, exit_status: int) -> Path:
    """Shim at bin_dir/git: exits exit_status at once when any argument
    matches the shell `case` pattern arg_pattern, printing nothing; every
    other invocation proxies to the real git. Stands in for a capped call
    whose wrapper reports a cap-kill status without waiting for the cap.
    Callers must set REAL_GIT in the shim's environment to a real git
    binary path (e.g. via shutil.which("git"))."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        'for arg in "$@"; do\n'
        '  case "$arg" in\n'
        f'    {arg_pattern})\n'
        f'      exit {exit_status}\n'
        '      ;;\n'
        '  esac\n'
        'done\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def _make_git_recording_argv(bin_dir: Path, log_file: Path, action: str = "") -> Path:
    """Shim at bin_dir/git: appends this invocation's argv as one line to
    log_file, then runs the caller-supplied bash `action` -- which sees that
    argv as "$@" and this invocation's own running count (log_file's line
    count immediately after the append, so no second state file exists to
    drift) as $count -- before exec'ing the real git. `action` defaults to a
    no-op, for callers that only need the recorded log. Callers must set
    REAL_GIT in the shim's environment to a real git binary path (e.g. via
    shutil.which("git"))."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    shim = bin_dir / "git"
    shim.write_text(
        '#!/bin/bash\n'
        f'LOG_FILE="{log_file}"\n'
        'printf "%s\\n" "$*" >> "$LOG_FILE"\n'
        'count=$(wc -l < "$LOG_FILE")\n'
        f'{action}\n'
        'exec "$REAL_GIT" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


def build_path_without(binary: str, farm_dir: Path) -> str:
    """Build a PATH string mirroring the real PATH via a symlink farm, with
    `binary` omitted, inside the caller-supplied (already-created) `farm_dir`.

    A full mirror (not a hand-picked minimal tool subset) is deliberate:
    under-symlinking is a silent false pass here — a hook denying because
    some OTHER required tool is missing looks identical to the hook denying
    correctly for the binary this test actually targets. First real PATH
    directory wins on a duplicate basename, mirroring normal PATH shadowing
    order; unreadable directories are skipped rather than raising.

    Callers own `farm_dir`'s lifetime and any caching strategy (e.g.
    session-scoped memoization) — this function only builds the farm once
    per call.
    """
    seen: set[str] = set()
    for real_dir in os.environ.get("PATH", "").split(os.pathsep):
        if not real_dir:
            continue
        try:
            entries = os.listdir(real_dir)
        except OSError:
            continue
        for name in entries:
            if name == binary or name in seen:
                continue
            src = Path(real_dir) / name
            try:
                if not os.access(src, os.X_OK):
                    continue
            except OSError:
                continue
            seen.add(name)
            try:
                (farm_dir / name).symlink_to(src)
            except OSError:
                continue
    path_str = str(farm_dir)
    assert shutil.which(binary, path=path_str) is None, (
        f"{binary}: still resolvable on the built PATH {path_str!r} — farm construction bug"
    )
    return path_str


# -- Scaled timeout(1) shim for cap-boundary tests ---------------------------
#
# A cap-boundary test proves timeout(1) actually killed a hung command by
# installing a fake `timeout` into its own PATH-prepended bin dir that
# divides the caller-supplied duration by TIMEOUT_SCALE_DIVISOR before
# running the real binary, so the test waits a fraction of the production
# cap. Production hooks never see this shim, so their behavior is
# unaffected. The same shim is the suite's evidence a cap fired: it appends
# a "<duration> <command>" line to a `started` log before running the real
# binary, and appends the same line to a `completed` log only when the real
# binary reports the command finished on its own. A line present in
# `started` with no matching `completed` entry is a killed invocation.
# The command field lets a test distinguish which stage of a same-duration
# pipe was actually killed, rather than only that some stage at that
# duration was.

TIMEOUT_SCALE_DIVISOR = 3

_SCALED_TIMEOUT_MARKER_DIRNAME = "scaled-timeout-markers"


def write_scaled_timeout_shim(bin_dir: Path) -> bool:
    """Write a `timeout` shim into `bin_dir` that divides an integer
    duration by TIMEOUT_SCALE_DIVISOR before running the real
    timeout(1)/gtimeout(1), recording each invocation's duration in a
    started/completed log pair under bin_dir. Returns False, writing
    nothing, when neither binary is on PATH -- every scaled value is then
    left unscaled by the caller.

    Only a `timeout` fake is written, never a `gtimeout` one:
    _lib_capped_for probes `timeout` first, so a `gtimeout` fake would be
    unreachable on every host.
    """
    real_timeout = shutil.which("timeout") or shutil.which("gtimeout")
    if real_timeout is None:
        return False
    marker_dir = bin_dir / _SCALED_TIMEOUT_MARKER_DIRNAME
    marker_dir.mkdir(exist_ok=True)
    started_log = marker_dir / "started"
    completed_log = marker_dir / "completed"
    shim_path = bin_dir / "timeout"
    # A surviving symlink (the two closed-PATH sites that symlink the real
    # timeout binary into place) would send write_text through to the real
    # binary rather than replacing it.
    shim_path.unlink(missing_ok=True)
    shim_path.write_text(
        "#!/bin/bash\n"
        "# Test-only: scales an integer timeout(1) duration down so a cap-boundary test waits a fraction of the production cap.\n"
        "# Only a 1-9-leading integer scales: bash arithmetic reads a leading zero as an octal prefix,\n"
        "# so every other $1 runs at the caller's own duration.\n"
        "# A leading `-k <n>` pair (_lib_capped_for's SIGKILL grace) is stripped before that check runs, so it isn't\n"
        "# misread as the duration itself. A 1-9-leading integer grace is scaled by the same divisor before being\n"
        "# re-attached below; any other grace is forwarded unscaled.\n"
        "grace=()\n"
        'if [ "$1" = "-k" ]; then\n'
        '  grace=(-k "$2")\n'
        "  shift 2\n"
        "fi\n"
        'if [[ "$1" =~ ^[1-9][0-9]*$ ]]; then\n'
        '  requested="$1"\n'
        f"  scaled_ms=$(( requested * 1000 / {TIMEOUT_SCALE_DIVISOR} ))\n"
        "  printf -v scaled '%d.%03d' \"$(( scaled_ms / 1000 ))\" \"$(( scaled_ms % 1000 ))\"\n"
        '  if [ "${#grace[@]}" -gt 0 ] && [[ "${grace[1]}" =~ ^[1-9][0-9]*$ ]]; then\n'
        f"    scaled_grace_ms=$(( grace[1] * 1000 / {TIMEOUT_SCALE_DIVISOR} ))\n"
        "    printf -v scaled_grace '%d.%03d' \"$(( scaled_grace_ms / 1000 ))\" \"$(( scaled_grace_ms % 1000 ))\"\n"
        '    grace=(-k "$scaled_grace")\n'
        "  fi\n"
        "  shift\n"
        "  # One appended line per invocation, so a hook making several capped calls is counted rather than overwritten.\n"
        "  # $1 is the wrapped command; the shift above already consumed the duration.\n"
        f"  printf '%s %s\\n' \"$requested\" \"${{1##*/}}\" >> {shlex.quote(str(started_log))}\n"
        f'  {shlex.quote(str(real_timeout))} "${{grace[@]}}" "$scaled" "$@"\n'
        "  status=$?\n"
        "  # The statuses in _lib_capped_for's header (_lib.sh) count as the cap firing;\n"
        "  # a child's own 137/143 signal-death is indistinguishable, so an external kill counts the same way.\n"
        "  [[ $status -eq 124 || $status -eq 137 || $status -eq 143 ]] || \\\n"
        f"    printf '%s %s\\n' \"$requested\" \"${{1##*/}}\" >> {shlex.quote(str(completed_log))}\n"
        '  exit "$status"\n'
        "fi\n"
        f'exec {shlex.quote(str(real_timeout))} "${{grace[@]}}" "$@"\n'
    )
    shim_path.chmod(0o755)
    return True


def _scaled_timeout_log_counts(bin_dir: Path, name: str) -> Counter[str]:
    log = bin_dir / _SCALED_TIMEOUT_MARKER_DIRNAME / name
    if not log.exists():
        return Counter()
    return Counter(log.read_text().splitlines())


def caps_that_fired(bin_dir: Path) -> Counter[str]:
    """Caller-supplied "<duration> <command>" pairs whose invocation started
    and never completed."""
    return _scaled_timeout_log_counts(bin_dir, "started") - _scaled_timeout_log_counts(bin_dir, "completed")


def scaled_cap(production_seconds: float) -> float:
    """The scaled `timeout` shim's own view of a production cap -- what it
    actually enforces once write_scaled_timeout_shim is installed."""
    return production_seconds / TIMEOUT_SCALE_DIVISOR


def scaled_shim_sleep(seconds: float) -> int:
    """Round a shim sleep up so it keeps outlasting its scaled cap --
    rounding down could let the sleep finish before a working cap fires,
    turning a real regression into a false pass."""
    return math.ceil(seconds / TIMEOUT_SCALE_DIVISOR)


def scaled_under_cap_sleep(seconds: float) -> float:
    """Divide with no rounding, for the one site whose shim sleep must
    finish inside its scaled cap rather than outlast it -- rounding up
    would walk that sleep toward the cap it has to stay under."""
    return seconds / TIMEOUT_SCALE_DIVISOR


def _cap_key(production_cap: float) -> str:
    """Format a production cap the way the scaled `timeout` shim recorded
    it -- the bare, unpadded integer string a hook actually passed to
    timeout(1)."""
    return str(int(production_cap))


def _log_key_duration(log_key: str) -> str:
    """The duration field of a scaled-shim "<duration> <command>" log key."""
    duration, _, _command = log_key.partition(" ")
    return duration


def _counts_at_duration(counts: Counter[str], production_cap: float) -> int:
    """Sum a log Counter's values across every command recorded at
    production_cap's duration, aggregating across a pipe's stages."""
    duration_key = _cap_key(production_cap)
    return sum(count for log_key, count in counts.items() if _log_key_duration(log_key) == duration_key)


@contextmanager
def assert_cap_engaged(
    bin_dir: Path,
    production_cap: float | None = None,
    killed_calls: int = 1,
    command: str | None = None,
):
    """Assert a timeout(1) cap killed the wrapped block's capped call(s),
    read from the scaled `timeout` shim's started/completed logs rather
    than a wall-clock floor. The shim logs a call as fired when it returns
    124, 137 or 143, the statuses _lib_capped_for's header in _lib.sh lists
    for a cap kill. 137 and 143 also occur as a child's own signal-death
    status, so a fired call is evidence of a cap kill, not proof.

    Snapshots both logs on entry so a second hook run inside the same
    bin_dir isn't double-counted. Raises when the shim recorded nothing at
    all (never invoked), with a message distinct from "every invocation
    completed on its own" (invoked, but nothing killed).

    command: with production_cap, asserts the kill count for that exact
    (duration, command) pair instead of summing across commands -- needed
    to tell which stage of a same-duration piped pair was killed.
    """
    started_before = _scaled_timeout_log_counts(bin_dir, "started")
    completed_before = _scaled_timeout_log_counts(bin_dir, "completed")
    yield
    started_delta = _scaled_timeout_log_counts(bin_dir, "started") - started_before
    completed_delta = _scaled_timeout_log_counts(bin_dir, "completed") - completed_before
    if not started_delta:
        raise AssertionError(
            f"expected a capped timeout(1) call inside {bin_dir}, but the scaled "
            "timeout shim was never invoked"
        )
    fired_delta = started_delta - completed_delta
    if not fired_delta:
        raise AssertionError(
            f"expected a capped timeout(1) call to be killed inside {bin_dir}, but "
            "every invocation completed on its own"
        )
    if command is not None:
        assert production_cap is not None, "assert_cap_engaged(command=...) requires production_cap"
        got = fired_delta[f"{_cap_key(production_cap)} {command}"]
        cap_desc = f"at cap {_cap_key(production_cap)}s for {command!r} "
    elif production_cap is None:
        got = sum(fired_delta.values())
        cap_desc = ""
    else:
        got = _counts_at_duration(fired_delta, production_cap)
        cap_desc = f"at cap {_cap_key(production_cap)}s "
    assert got == killed_calls, (
        f"expected {killed_calls} kill(s) {cap_desc}inside {bin_dir}, got {dict(fired_delta)}"
    )


@contextmanager
def assert_cap_not_engaged(bin_dir: Path, production_cap: float | None = None):
    """The inverse of assert_cap_engaged: the scaled `timeout` shim must
    have run inside the wrapped block (at `production_cap`, when given) and
    nothing it wrapped may have been killed. For the one site whose shim
    sleep must finish inside its cap rather than outlast it.
    """
    started_before = _scaled_timeout_log_counts(bin_dir, "started")
    fired_before = caps_that_fired(bin_dir)
    yield
    started_delta = _scaled_timeout_log_counts(bin_dir, "started") - started_before
    if not started_delta:
        raise AssertionError(
            f"expected the scaled timeout shim to run inside {bin_dir}, but it was "
            "never invoked"
        )
    if production_cap is not None:
        got = _counts_at_duration(started_delta, production_cap)
        assert got >= 1, (
            f"expected an invocation at cap {_cap_key(production_cap)}s inside {bin_dir}, "
            f"got {dict(started_delta)}"
        )
    fired_delta = caps_that_fired(bin_dir) - fired_before
    assert not fired_delta, (
        f"expected no capped timeout(1) kill inside {bin_dir}, but got {dict(fired_delta)}"
    )


# (first_line, expect_consult, id) rows behind plan-architect consult
# classification, reused by test_log_reviewer_round.py's bash-latch test and
# test_transcript_analysis.py's Python-classifier test. Proves the two
# runtimes agree on classification behaviorally, not that their source text
# matches byte-for-byte -- a byte-equality assertion across the literal sites
# would still pass with the Python `!=` comparison inverted to `==`. No
# pytest import here (see module docstring), so each test file wraps these
# rows in pytest.param(...) at its own @pytest.mark.parametrize call site.
CONSULT_CLASSIFICATION_TABLE: list[tuple[str, bool, str]] = [
    ("MODE=consult", True, "mode_consult"),
    ("MODE=plan-sections", False, "mode_plan_sections"),
    ("", True, "empty_first_line"),
    ("MODE=plna-sections", True, "typo_mode_value"),
    ("Just look at the plan and tell me if it's sound.", True, "no_mode_line"),
    ("MODE=plan-sections ", True, "mode_plan_sections_trailing_space"),
    ("Some preamble.\nMODE=plan-sections", True, "mode_plan_sections_not_first_line"),
    ("MODE=plan-sections\r\n## Section A", True, "mode_plan_sections_crlf"),
]
