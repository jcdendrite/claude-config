"""Pure helpers and path constants shared across hook, skill, and script test files.

No pytest decorators here — this is a plain Python module. Import
explicitly from each test file that needs these symbols.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

CLAUDE_DIR = Path(__file__).resolve().parent.parent

HOOKS_DIR = CLAUDE_DIR / "hooks"
SKILLS_DIR = CLAUDE_DIR / "skills"
SCRIPTS_DIR = CLAUDE_DIR / "scripts"

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

    Full parent env is intentionally inherited even when home or extra_env is
    set, because hook scripts depend on PATH to locate tool binaries.
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

    Silent exit (exit 0, empty stdout) maps to "allow" to match the hook
    protocol, where absence of output means "no opinion".

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
    return payload["hookSpecificOutput"]["permissionDecision"]


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
) -> dict:
    payload: dict = {"tool_name": "Bash", "tool_input": {"command": command}}
    if session_id is not None:
        payload["session_id"] = session_id
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


def edit_input(file_path: str) -> dict:
    return {
        "tool_name": "Edit",
        "tool_input": {"file_path": file_path, "old_string": "a", "new_string": "b"},
    }


def write_input(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": "x"}}


def multiedit_input(file_path: str) -> dict:
    return {"tool_name": "MultiEdit", "tool_input": {"file_path": file_path, "edits": []}}


def exitplanmode_input(plan_file_path: str = "/home/user/.claude/plans/test-plan.md") -> dict:
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


def agent_input(session_id: str | None = None) -> dict:
    payload: dict = {
        "tool_name": "Agent",
        "tool_input": {"description": "test", "prompt": "test"},
    }
    if session_id is not None:
        payload["session_id"] = session_id
    return payload


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


def marker_path(home: Path, repo: Path, session_id: str = DEFAULT_TEST_SESSION_ID) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "review-markers" / f"{repo_hash}.{session_id}"


def staged_diff_hash(repo: Path) -> str:
    diff = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, check=True
    ).stdout
    return hashlib.sha256(diff).hexdigest()


def write_marker(
    home: Path,
    repo: Path,
    diff_hash: str,
    session_id: str = DEFAULT_TEST_SESSION_ID,
) -> Path:
    marker = marker_path(home, repo, session_id)
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


def plan_review_marker_path(home: Path, repo: Path, session_id: str) -> Path:
    repo_hash = hashlib.sha256(git_toplevel(repo).encode()).hexdigest()
    return home / ".claude" / "plan-review-markers" / f"{repo_hash}.{session_id}"


def write_plan_review_marker(home: Path, repo: Path, session_id: str) -> Path:
    marker = plan_review_marker_path(home, repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("reviewed\n")
    return marker


def skill_review_marker_path(home: Path, repo: Path, session_id: str = DEFAULT_TEST_SESSION_ID) -> Path:
    repo_hash = subprocess.run(
        ["sha256sum"],
        input=git_toplevel(repo).encode(),
        capture_output=True,
    ).stdout.decode().split()[0]
    return home / ".claude" / "skill-review-markers" / f"{repo_hash}.{session_id}"


def write_skill_review_marker(home: Path, repo: Path, session_id: str = DEFAULT_TEST_SESSION_ID) -> None:
    diff = subprocess.run(
        ["git", "diff", "--cached", "--", "claude/.claude/skills/**/SKILL.md", "plugins/*/skills/**/SKILL.md"],
        capture_output=True,
        check=True,
        cwd=repo,
    ).stdout
    diff_hash = hashlib.sha256(diff).hexdigest()
    marker = skill_review_marker_path(home, repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(diff_hash + "\n")


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


def run_skill_command(command: str, cwd: Path, isolated_home: Path) -> None:
    """Run a SKILL.md-extracted bash command in a sandboxed $HOME."""
    _symlink_if_absent(isolated_home / ".claude" / "scripts" / "marker.sh", SCRIPTS_DIR / "marker.sh")
    _symlink_if_absent(isolated_home / ".claude" / "hooks" / "_lib.sh", HOOKS_DIR / "_lib.sh")
    subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=_build_subprocess_env(isolated_home, None),
        check=True,
    )
