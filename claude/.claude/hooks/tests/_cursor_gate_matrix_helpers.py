"""The gate matrix's harness: the registrations, the world a hook runs in, the deny-trigger table and the hook runners."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from helpers import HOOKS_DIR, REPO_ROOT, _hook_class

from ._cursor_payloads import (
    CLAUDE_PROCESS_ENV_NAMES,
    CONTRACT_VERSION,
    PARITY_EXCEPTIONS_JSON,
    TOOL_MAP_JSON,
    ShimEnv,
    assert_outside_any_repository,
    hook_args,
    load_json,
    recorded_tool_paths,
    registration_key,
    settings_registrations,
    without_git_environment,
)

GATE_CLASSES = ("gate", "turn-gate", "batch-gate")
# A project choice, the same bound as run_shim's default: a hook blocked on a child fails as a TimeoutExpired.
DIRECT_HOOK_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class Registration:
    event: str
    matcher: str
    script: str
    hook_class: str

    @property
    def key(self) -> str:
        return registration_key(self.event, self.matcher, self.script)

    @property
    def path(self) -> Path:
        return HOOKS_DIR / self.script

    @property
    def matcher_tokens(self) -> set[str]:
        return set(self.matcher.split("|"))


def _registrations() -> list[Registration]:
    seen: dict[str, Registration] = {}
    for event, matcher, script in settings_registrations():
        registration = Registration(event, matcher, script, _hook_class(HOOKS_DIR / script) or "")
        seen.setdefault(registration.key, registration)
    return list(seen.values())


ALL_REGISTRATIONS = _registrations()


def _exceptions() -> dict[str, dict]:
    return {e["key"]: e for e in load_json(PARITY_EXCEPTIONS_JSON)["entries"] if e["kind"] == "registration"}


EXCEPTIONS = _exceptions()


# Time values are masked before a file is hashed, so two runs that write the same line at different seconds compare equal.
# No id is normalized.
TIME_VALUE_PATTERN = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class World:
    """The directories a hook can write, rebuilt from one pristine copy before each run so two runs share absolute paths."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "world"
        self.pristine = base / "pristine"
        self.home = self.root / "home"
        self.config = self.root / "config"
        self.tmp = self.root / "tmp"
        self.repo = self.root / "repo"
        self.stubs = self.root / "stubs"
        self.outside = base / "outside"
        self.outside.mkdir()
        for directory in (self.home, self.config, self.tmp, self.repo, self.stubs):
            directory.mkdir(parents=True)
        (self.config / "cursor-adapter-state").mkdir(mode=0o700)
        self.home = self.home.resolve()
        self.config = self.config.resolve()
        self.tmp = self.tmp.resolve()
        self.repo = self.repo.resolve()
        subprocess.run(
            ["git", "init", "-q", str(self.repo)], check=True, capture_output=True, env=without_git_environment(os.environ)
        )
        (self.repo / "src").mkdir()
        (self.repo / "README.md").write_text("fixture\n")
        # A second repository that is no workspace root, with a clean tree, for the leading-cd rows.
        clean_repo = base / "clean-repo"
        clean_repo.mkdir()
        self.clean_repo = clean_repo.resolve()
        subprocess.run(
            ["git", "init", "-q", str(self.clean_repo)], check=True, capture_output=True, env=without_git_environment(os.environ)
        )
        (self.clean_repo / "README.md").write_text("clean\n")
        self.plain_directory = base / "plain-directory"
        self.plain_directory.mkdir()
        self._write_stubs()
        self._write_install_record()
        shutil.copytree(self.root, self.pristine, symlinks=True)

    def _write_stubs(self) -> None:
        log = self.stubs / "calls.log"
        real_git = shutil.which("git")
        for name in ("curl", "wget", "gh", "nc", "ssh", "scp", "rsync", "detached-probe-command"):
            stub = self.stubs / name
            stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\ntouch "{self.stubs}/sentinel-{name}"\nexit 0\n')
            stub.chmod(stat.S_IRWXU)
        git_stub = self.stubs / "git"
        git_stub.write_text(
            "#!/bin/sh\n"
            f'case "$*" in *fetch*|*pull*|*push*|*clone*|*ls-remote*) echo "git $*" >> "{log}"; exit 0;; esac\n'
            f'exec "{real_git}" "$@"\n'
        )
        git_stub.chmod(stat.S_IRWXU)

    def reset(self) -> None:
        shutil.rmtree(self.root)
        shutil.copytree(self.pristine, self.root, symlinks=True)

    def apply_setup(self, setup: Callable[[World], None] | None) -> None:
        """Run `setup` on the live tree, then make the result the pristine copy every later reset restores."""
        if setup is None:
            return
        setup(self)
        shutil.rmtree(self.pristine)
        shutil.copytree(self.root, self.pristine, symlinks=True)

    @property
    def watched(self) -> dict[str, Path]:
        return {"home": self.home, "config": self.config, "tmp": self.tmp, "repo": self.repo}

    def snapshot(self, ignore_state: bool) -> dict[str, str]:
        files: dict[str, str] = {}
        for label, directory in self.watched.items():
            for path in sorted(directory.rglob("*")):
                relative = path.relative_to(self.root)
                # The state dir's files are the shim's log.
                if (
                    ".git/index" in str(relative)
                    or relative.name == "cursor-adapter-state"
                    or (ignore_state and "cursor-adapter-state" in relative.parts)
                ):
                    continue
                if path.is_file() and not path.is_symlink():
                    files[f"{label}:{relative}"] = hashlib.sha256(
                        TIME_VALUE_PATTERN.sub(b"<time>", path.read_bytes())
                    ).hexdigest()
                else:
                    files[f"{label}:{relative}"] = "dir" if path.is_dir() else "link"
        return files

    def network_calls(self) -> list[str]:
        log = self.stubs / "calls.log"
        return log.read_text().splitlines() if log.exists() else []

    def cursor_env(self, extra: dict | None = None) -> dict:
        """The environment of a Cursor hook: no Claude Code marker, no GIT_* name, and the stubs first on PATH."""
        env = without_git_environment(
            {k: v for k, v in os.environ.items() if k not in CLAUDE_PROCESS_ENV_NAMES and k != "CLAUDE_CONFIG_DIR"}
        )
        env.update(
            {
                "HOME": str(self.home),
                "TMPDIR": str(self.tmp),
                "CURSOR_PROJECT_DIR": str(self.repo),
                "CLAUDE_PROJECT_DIR": str(self.repo),
                "CURSOR_VERSION": "0.0.0",
                "PATH": f"{self.stubs}:{os.environ['PATH']}",
            }
        )
        env.update(extra or {})
        return env

    def _write_install_record(self) -> None:
        """The record names the stubs, so the PATH the shim fixes from it holds them."""
        stub_paths = {p.name: str(p) for p in self.stubs.iterdir() if p.is_file() and p.name not in ("calls.log",)}
        tool_paths = {**stub_paths, **{k: v for k, v in recorded_tool_paths().items() if k not in stub_paths}}
        record_dir = self.home / ".cursor" / "cursor-adapter"
        record_dir.mkdir(parents=True)
        self.record_path = record_dir / "install.json"
        self.record_path.write_text(
            json.dumps(
                {
                    "routed_config_dir": str(self.config),
                    "contract_version": CONTRACT_VERSION,
                    "tool_paths": tool_paths,
                    "tool_map": load_json(TOOL_MAP_JSON),
                }
            )
        )

    def shim_env(self) -> ShimEnv:
        return ShimEnv(home=self.home, config_dir=self.config, record_path=self.record_path)


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _const(value: dict) -> Callable[[World], dict]:
    return lambda world: value


def _stage_a_change(world: World) -> None:
    (world.repo / "README.md").write_text("fixture\nstaged line\n")
    subprocess.run(
        ["git", "-C", str(world.repo), "add", "README.md"],
        check=True,
        capture_output=True,
        env=without_git_environment(os.environ),
    )


def _memory_path(world: World, name: str) -> str:
    """A path under the routed config dir's projects directory, where the gate looks. Raw arms have no routed dir."""
    return str(world.config / "projects" / "placeholder-project" / "memory" / name)


def _write_memory_index(world: World) -> None:
    index = Path(_memory_path(world, "MEMORY.md"))
    index.parent.mkdir(parents=True)
    index.write_text("- index\n")


def _require_worktree_sentinel(world: World) -> None:
    sentinel = world.repo / ".claude" / "worktree-required"
    sentinel.parent.mkdir(exist_ok=True)
    sentinel.write_text("")


def _existing_file_in_the_main_checkout(world: World) -> None:
    _require_worktree_sentinel(world)
    (world.repo / "src" / "existing.txt").write_text("old text\n")


@dataclass(frozen=True)
class Trigger:
    """A deny trigger modeled on the gate's own tests, and a near miss the gate must let through.

    `overwrites` is the text of the file a Write replaces, and `setup` builds the state the trigger needs.
    `inert` is true when a missed deny changes nothing outside the scratch repository the trigger runs in.
    `drawn` is true when the attended acceptance run fires the trigger against the real gate.
    Neither has a default, so a Trigger built without one is a construction error.
    """

    name: str
    tool: str
    deny_input: Callable[[World], dict]
    near_miss_input: Callable[[World], dict]
    inert: bool = field(kw_only=True)
    drawn: bool = field(kw_only=True)
    raw_class: str = "no-op"
    setup: Callable[[World], None] | None = None
    overwrites: str | None = None

    @property
    def tool_class(self) -> str:
        if self.tool == "Write":
            return "Write-overwrite" if self.overwrites is not None else "Write-create"
        return self.tool


def _bash(command: str) -> dict:
    return {"command": command}


def _read(path: str) -> dict:
    return {"file_path": path}


# Each trigger's command or path is modeled on a deny case in the gate's own test file.
TRIGGERS: dict[str, tuple[Trigger, ...]] = {
    "block-gh-pr-merge.sh": (
        Trigger(
            "merge",
            "Shell",
            _const(_bash("gh pr merge 291 --squash")),
            _const(_bash("gh pr view 291")),
            inert=False,
            drawn=False,
        ),
    ),
    "deny-credential-bash-reads.sh": (
        Trigger(
            "cat-credentials",
            "Shell",
            _const(_bash("cat /foo/credentials.json")),
            _const(_bash("cat /foo/notes.txt")),
            inert=True,
            drawn=True,
        ),
    ),
    "deny-default-branch-push.sh": (
        Trigger(
            "push-main",
            "Shell",
            _const(_bash("git push origin main")),
            _const(_bash("git push origin feature")),
            inert=False,
            drawn=False,
        ),
    ),
    "deny-network-installs.sh": (
        Trigger(
            "npm-install",
            "Shell",
            _const(_bash("npm install lodash")),
            _const(_bash("npm run build")),
            inert=False,
            drawn=False,
        ),
    ),
    "deny-repo-relocation.sh": (
        Trigger(
            "mv-repo",
            "Shell",
            lambda world: _bash(f"mv {REPO_ROOT} /tmp/elsewhere"),
            _const(_bash("mv a.txt b.txt")),
            inert=False,
            drawn=False,
        ),
    ),
    "deny-escaped-backticks-in-pr-body.sh": (
        Trigger(
            "escaped-backtick",
            "Shell",
            _const(_bash(r"gh pr create --body 'body with \`escaped\` backticks'")),
            _const(_bash("gh pr create --body 'clean body no escapes'")),
            inert=False,
            drawn=False,
            raw_class="denies",
        ),
    ),
    "require-code-review.sh": (
        Trigger(
            "commit-without-review",
            "Shell",
            _const(_bash("git commit -m x")),
            _const(_bash("git status")),
            inert=False,
            drawn=False,
            setup=_stage_a_change,
        ),
    ),
    "require-worktree-for-git-writes.sh": (
        Trigger(
            "commit-in-the-main-checkout",
            "Shell",
            _const(_bash("git commit -m x")),
            _const(_bash("git status")),
            inert=False,
            drawn=False,
            raw_class="denies",
            setup=_require_worktree_sentinel,
        ),
    ),
    # The two Write rows below are the only drawn rows whose missed deny changes a file, and it stays under `world.repo`.
    "require-worktree-for-file-writes.sh": (
        Trigger(
            "create-in-the-main-checkout",
            "Write",
            lambda world: {"file_path": str(world.repo / "src" / "created.txt"), "content": "text"},
            lambda world: {"file_path": str(world.tmp / "outside-the-repo.txt"), "content": "text"},
            inert=True,
            drawn=True,
            raw_class="denies",
            setup=_require_worktree_sentinel,
        ),
        Trigger(
            "overwrite-in-the-main-checkout",
            "Write",
            lambda world: {"file_path": str(world.repo / "src" / "existing.txt"), "content": "new text"},
            lambda world: {"file_path": str(world.tmp / "outside-the-repo.txt"), "content": "text"},
            inert=True,
            drawn=True,
            raw_class="denies",
            setup=_existing_file_in_the_main_checkout,
            overwrites="old text\n",
        ),
    ),
    "deny-env-reads.sh": (
        Trigger(
            "env-file",
            "Read",
            _const(_read("/foo/.env")),
            _const(_read("/foo/notes.txt")),
            inert=True,
            drawn=False,
            raw_class="denies",
        ),
    ),
    "deny-credential-file-reads.sh": (
        Trigger(
            "credentials-file",
            "Read",
            _const(_read("/foo/credentials.json")),
            _const(_read("/foo/notes.txt")),
            inert=True,
            drawn=True,
            raw_class="denies",
        ),
    ),
    "require-memory-skill.sh": (
        Trigger(
            "new-topic-file",
            "Write",
            lambda world: {"file_path": _memory_path(world, "new-topic.md"), "content": "topic"},
            lambda world: {"file_path": str(world.repo / "src" / "a.txt"), "content": "text"},
            inert=False,
            drawn=False,
        ),
        Trigger(
            "index-overwrite",
            "Write",
            lambda world: {"file_path": _memory_path(world, "MEMORY.md"), "content": "- index\n- more\n"},
            lambda world: {"file_path": str(world.repo / "src" / "a.txt"), "content": "text"},
            inert=False,
            drawn=False,
            setup=_write_memory_index,
            overwrites="- index\n",
        ),
    ),
    "deny-no-op-dispatch.sh": (
        Trigger(
            "wait-only",
            "Task",
            _const({"description": "wait", "prompt": "wait", "subagent_type": "general-purpose"}),
            _const(
                {
                    "description": "run tests",
                    "prompt": "Run the unit tests for the parser module and report the failures.",
                    "subagent_type": "general-purpose",
                }
            ),
            inert=True,
            drawn=True,
            raw_class="denies",
        ),
    ),
}


# Gate scripts whose registration has no trigger above, each with the reason. A new gate, or a new trigger, forces an edit here.
NO_TRIGGER_YET = {
    "require-diff-size-prompt.sh": "denies a commit only when the branch's cumulative diff passes a size threshold; "
    "its roster deny keys on agent_type, which the shim does not forward, so that deny is inert under the shim",
    "guard-settings-session-keys.sh": "denies a commit only when a staged settings.json holds session keys",
    "deny-private-project-refs.sh": "denies only against a populated private-project blocklist",
    "require-stow-reminder.sh": "denies a PR open or edit that adds a stowed path to this repository",
    "require-respond-pr.sh": "denies PR comment fetches and posts until /respond-pr has run, which needs a marker",
    "check-skill-length.sh": "denies a commit only when a staged SKILL.md passes its ceiling",
    "check-claude-md-length.sh": "denies a commit only when a staged CLAUDE.md or AGENTS.md passes its limit",
    "require-ready-for-review.sh": "denies a push, ready or create only against an open PR or a missing completion marker",
    "deny-pii-in-commits.sh": "denies a commit only when its added lines hold personal data in a staged diff",
    "deny-invisible-commit-content.sh": "denies a commit only when its staged content holds invisible characters",
    "enforce-marker-script-shape.sh": "denies only a marker write or script of the wrong shape, which needs marker state; "
    "its subagent bars key on agent_type and agent_id, which the shim does not forward, so they are inert under the shim",
    "deny-reviewer-tree-mutation.sh": "keys on agent_type, which Cursor payloads do not carry until agent attribution lands",
    "require-plan-review.sh": "denies a Write only while a modified plan awaits review, which needs plan state",
    "deny-plan-writer-off-plan-writes.sh": "keys on agent_type, which Cursor payloads do not carry until attribution lands; "
    "the name outcomes run through the real gate with an injected agent_type in TestPlanWriterGateNameOutcomes",
    "require-routing-read.sh": "denies only during an active plan-review session, which needs session marker state",
    "require-architect-consult.sh": "denies a reviewer-persona dispatch only on a branch with plan state",
    "deny-data-file-reads.sh": "denies only a data-shaped file read, which needs a data file on disk",
}
# Translated Cursor tool classes that no gate trigger covers, each with the reason.
NO_TRIGGER_TOOL_CLASSES = {
    "Delete": "the only gates that inspect an rm key on agent_type, and Delete becomes `rm -- <path>` under Bash",
}
TRIGGER_TOOL_CLASSES = {"Shell", "Read", "Write-create", "Write-overwrite", "Task"}


def _row_matcher(registration: Registration) -> str:
    return registration.matcher if registration.matcher != "*" else ""


def _arm_args(registration: Registration) -> list[str]:
    return hook_args(
        registration.event,
        script=registration.path,
        matcher=_row_matcher(registration) or None,
        label=registration.path.stem,
        fail_closed=registration.hook_class in GATE_CLASSES,
    )


def _run_outside(world: World, command: list[str], payload: dict) -> subprocess.CompletedProcess:
    """Run `command` under Cursor's environment."""
    environment = world.cursor_env()
    assert_outside_any_repository(world.outside, environment)
    return subprocess.run(
        command,
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=False,
        cwd=world.outside,
        env=environment,
        timeout=DIRECT_HOOK_TIMEOUT_SECONDS,
    )


def _direct(world: World, registration: Registration, payload: dict, *, cwd: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(registration.path)],
        input=json.dumps(payload).encode(),
        capture_output=True,
        check=False,
        cwd=cwd,
        env=env,
        timeout=DIRECT_HOOK_TIMEOUT_SECONDS,
    )


def _decision(result: subprocess.CompletedProcess) -> str:
    """allow | deny | ask | other | none for a direct run of a hook script. `none` is exit 0 with empty stdout."""
    if result.returncode == 2:
        return "deny"
    assert result.returncode == 0, (result.returncode, result.stderr)
    if not result.stdout.strip():
        return "none"
    output = json.loads(result.stdout)
    return output.get("hookSpecificOutput", {}).get("permissionDecision", "other")
