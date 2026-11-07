"""Tests for announce-resume-command.sh."""
from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest
from helpers import (
    HOOKS_DIR,
    _make_git_exiting_with_status,
    assert_cap_engaged,
    code_span_command,
    edit_input,
    multiedit_input,
    read_input,
    write_continuity_file,
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


def _install_resume_context_stub(home: Path) -> Path:
    """A `resume-context` that only records its argv, placed first on the hook's PATH so
    a dropped backtick escape in the hook's message assembly cannot run a real launcher."""
    stub_bin = home / ".resume-context-stub-bin"
    stub_bin.mkdir(exist_ok=True)
    stub = stub_bin / "resume-context"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> {shlex.quote(str(stub_bin / 'invocations.log'))}\n"
    )
    stub.chmod(0o755)
    return stub_bin


def _resume_context_stub_invocations(home: Path) -> str:
    log = home / ".resume-context-stub-bin" / "invocations.log"
    return log.read_text() if log.exists() else ""


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
    env["PATH"] = f"{_install_resume_context_stub(home)}:{env['PATH']}"
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


def _write_continuity_file(
    base: Path, rel_path: str, resume_cwd: str | None, *, kind: str = "handoff"
) -> Path:
    """A continuity file whose last section carries a resume command in the
    shape handoff/brief SKILL.md prescribes, with `--cwd` only when
    `resume_cwd` is given."""
    return write_continuity_file(base / rel_path, resume_cwd, kind=kind)


def _both_channels(result: subprocess.CompletedProcess) -> tuple[str, str]:
    """(systemMessage, additionalContext) of a hook run that announced."""
    payload = json.loads(result.stdout)
    return payload["systemMessage"], payload["hookSpecificOutput"]["additionalContext"]


_LOAD_PROVENANCE = (
    "The command names the written file by its file name, "
    "and the route compares the working directory of this session with the file's own resume command"
)
# Each substring is a fact a test asserts alone; the sentence constant composes it, so one edit moves both.
_SESSION_MEASURED_PROVENANCE = "computed from the resolved config dir and the working directory of this session"
_FILE_SOURCED_CWD_PROVENANCE = "the --cwd directory comes from the written file's own resume command"
_NOT_MEASURED_CLAUSE = "this hook did not measure it"
_SESSION_MEASURED_PROVENANCE_SENTENCE = f"The command was {_SESSION_MEASURED_PROVENANCE}"
_FILE_SOURCED_PROVENANCE_SENTENCE = (
    "The command was computed from the resolved config dir, except that "
    f"{_FILE_SOURCED_CWD_PROVENANCE} (it passed the path-character allowlist; {_NOT_MEASURED_CLAUSE})"
)
_USE_VERBATIM_CLAUSE = "use the command verbatim instead of reconstructing it"
_RELAY_SENTENCE = (
    "Relay this one route and its reason to the engineer "
    "with the command alone in its own fenced code block, name no other resume route, and run nothing yourself."
)
_RELAY_INSTRUCTION = f"{_USE_VERBATIM_CLAUSE}. {_RELAY_SENTENCE}"
_REFUSED_NAME_REASON = (
    "/resume-context cannot load this file by name, because the name is not one the slash form accepts "
    "(it must be a regular file directly in handoffs/ or briefs/, and its name must not start with a dash or a dot)."
)


def _assert_context_labels_the_cwd_as_file_sourced(context: str) -> None:
    """The model-facing text must not present a file-written --cwd as session-derived."""
    assert _FILE_SOURCED_CWD_PROVENANCE in context
    assert _NOT_MEASURED_CLAUSE in context
    assert _SESSION_MEASURED_PROVENANCE not in context
    assert _USE_VERBATIM_CLAUSE in context


def _run_hook_with_stubbed_file_target(
    tmp_path: Path, home: Path, fixture: Path, session_cwd: Path, file_target: str
) -> subprocess.CompletedProcess:
    """Runs a copy of the hook beside a `_lib.sh` that sources the real one, then
    replaces `_lib_continuity_file_resume_cwd` so it answers `file_target`
    regardless of the file. This isolates the hook's own handling of the
    helper's answer from the helper's validation."""
    stub_hooks_dir = tmp_path / "stub-hooks"
    stub_hooks_dir.mkdir()
    hook_copy = stub_hooks_dir / ANNOUNCE_HOOK.name
    shutil.copy2(ANNOUNCE_HOOK, hook_copy)
    (stub_hooks_dir / "_lib.sh").write_text(
        f". {shlex.quote(str(HOOKS_DIR / '_lib.sh'))}\n"
        "_lib_continuity_file_resume_cwd() { printf '%s' \"$STUBBED_FILE_TARGET\"; }\n"
    )
    return _run_hook_raw(
        hook_copy,
        write_input(str(fixture), cwd=str(session_cwd)),
        home=home,
        extra_env={"STUBBED_FILE_TARGET": file_target},
    )


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

@dataclass(frozen=True)
class _AnnounceArm:
    """One announce outcome. `{name}`, `{root}`, `{elsewhere}` and `{fixture}` in the strings
    stand for the file name, the linked worktree, the other directory the file names, and the file's path."""

    session: str  # "linked", "main" or "unknown"
    kind: str  # "handoff" or "brief"
    file_cwd: str | None  # "this-worktree", "elsewhere", "unusable" (an unreadable --cwd), or None for no --cwd
    command: str
    reason: str
    provenance: str
    relative_path: str | None = None  # the file's path under HOME; defaults to the kind's ordinary file name


_ANNOUNCE_ARMS = {
    "load-linked-handoff": _AnnounceArm(
        "linked", "handoff", "this-worktree", "/resume-context {name}",
        "the file's resume command targets this worktree (`{root}`)", _LOAD_PROVENANCE,
    ),
    "load-linked-brief": _AnnounceArm(
        "linked", "brief", "this-worktree", "/resume-context {name}",
        "the file's resume command targets this worktree (`{root}`)", _LOAD_PROVENANCE,
    ),
    "load-not-linked-handoff": _AnnounceArm(
        "main", "handoff", None, "/resume-context {name}",
        "the file's resume command names no directory, and this session is not in a linked worktree", _LOAD_PROVENANCE,
    ),
    "load-not-linked-brief": _AnnounceArm(
        "main", "brief", None, "/resume-context {name}",
        "the file's resume command names no directory, and this session is not in a linked worktree", _LOAD_PROVENANCE,
    ),
    "unknown": _AnnounceArm(
        "unknown", "handoff", None, "resume-context {fixture}",
        "/resume-context cannot confirm this file belongs to this worktree, "
        "because this session's worktree could not be determined.",
        _SESSION_MEASURED_PROVENANCE_SENTENCE,
    ),
    "mismatch-linked-usable-target": _AnnounceArm(
        "linked", "handoff", "elsewhere", "resume-context --cwd {elsewhere} {fixture}",
        "/resume-context cannot confirm this file belongs to this worktree, "
        "because its resume command targets `{elsewhere}`, not this worktree (`{root}`).",
        _FILE_SOURCED_PROVENANCE_SENTENCE,
    ),
    "mismatch-linked-unusable-target": _AnnounceArm(
        "linked", "handoff", None, "resume-context --cwd {root} {fixture}",
        "/resume-context cannot confirm this file belongs to this worktree, "
        "because its resume command does not name this worktree (`{root}`) as an absolute --cwd.",
        _SESSION_MEASURED_PROVENANCE_SENTENCE,
    ),
    "mismatch-not-linked-usable-target": _AnnounceArm(
        "main", "handoff", "elsewhere", "resume-context --cwd {elsewhere} {fixture}",
        "/resume-context cannot confirm this file belongs to this directory, "
        "because its resume command targets `{elsewhere}`, and this session is not in a linked worktree.",
        _FILE_SOURCED_PROVENANCE_SENTENCE,
    ),
    "mismatch-not-linked-unusable-target": _AnnounceArm(
        "main", "handoff", "unusable", "resume-context {fixture}",
        "/resume-context cannot confirm this file belongs to this directory, "
        "because its resume target could not be resolved to one directory, and this session is not in a linked worktree.",
        _SESSION_MEASURED_PROVENANCE_SENTENCE,
    ),
    "load-verdict-refused-name-linked": _AnnounceArm(
        "linked", "handoff", "this-worktree", "resume-context --cwd {root} {fixture}",
        _REFUSED_NAME_REASON, _SESSION_MEASURED_PROVENANCE_SENTENCE, ".claude/handoffs/-handoff.md",
    ),
    "load-verdict-refused-name-not-linked": _AnnounceArm(
        "main", "handoff", None, "resume-context {fixture}",
        _REFUSED_NAME_REASON, _SESSION_MEASURED_PROVENANCE_SENTENCE, ".claude/handoffs/-handoff.md",
    ),
}
_HANDOFF_LOAD_LEAD = "Resume in this session, because {reason}. Run /clear, then:"
_BRIEF_LOAD_LEAD = (
    "Resume in a session in {scope}, because {reason}. "
    "Start a new claude session there, or run /clear in this one once its work is done, then:"
)
_SHELL_LEAD = "{reason} Resume outside this session instead, in a new terminal on the machine that holds this file:"


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
        assert f"resume-context {fixture}" in payload["systemMessage"]

    def test_announces_command_for_brief_arm(self, isolated_home):
        """The checker-less sibling (briefs/*-task.md, with no check-brief.py
        counterpart) is pinned directly, not merely assumed to work because
        the handoff arm does."""
        fixture = _write_fixture(isolated_home, ".claude/briefs/example-task.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert str(fixture) in payload["systemMessage"]
        assert f"resume-context {fixture}" in payload["systemMessage"]

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
        assert f"resume-context {fixture}" in payload["systemMessage"]

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
        message, context = _both_channels(result)
        assert code_span_command(message) == "/resume-context example-handoff.md"
        for channel in (message, context):
            assert "--cwd" not in channel
            assert f"resume-context {fixture}" not in channel
        assert "`/resume-context example-handoff.md`" in context

    def test_cwd_absent_omits_cwd_flag_but_still_announces(self, isolated_home):
        fixture = _write_fixture(isolated_home, ".claude/handoffs/example-handoff.md")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "--cwd" not in payload["systemMessage"]
        assert f"resume-context {fixture}" in payload["systemMessage"]
        assert "--cwd" not in payload["hookSpecificOutput"]["additionalContext"]
        assert f"resume-context {fixture}" in payload["hookSpecificOutput"]["additionalContext"]

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
            extra_env={"GIT_CEILING_DIRECTORIES": str(tmp_path)},
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert code_span_command(message) == "/resume-context example-handoff.md"
        for channel in (message, context):
            assert "--cwd" not in channel
            assert f"resume-context {fixture}" not in channel
        assert "`/resume-context example-handoff.md`" in context

    # -----------------------------------------------------------------------
    # /resume-context advertisement
    # -----------------------------------------------------------------------

    def test_linked_worktree_with_matching_cwd_advertises_slash_form_in_both_channels(
        self, isolated_home, linked_worktree
    ):
        fixture = _write_continuity_file(
            isolated_home, ".claude/handoffs/example-handoff.md", str(linked_worktree.resolve())
        )
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert code_span_command(message) == "/resume-context example-handoff.md"
        for channel in (message, context):
            assert f"resume-context --cwd {linked_worktree.resolve()} {fixture}" not in channel

    def test_linked_worktree_cwd_reached_through_a_symlink_gets_the_shell_only_announce(
        self, isolated_home, linked_worktree, tmp_path
    ):
        """Expected difference from the script: the hook compares the file's raw
        --cwd with the raw git root, so a symlinked spelling is withheld here,
        while resume-context-same-session.sh canonicalizes both and loads it
        (see test_cwd_reached_through_a_symlink_still_loads)."""
        link = tmp_path / "worktree-link"
        link.symlink_to(linked_worktree.resolve())
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", str(link))
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
        assert "/resume-context cannot confirm this file belongs to this worktree" in message
        assert f"its resume command targets `{link}`, not this worktree" in message
        assert f"resume-context --cwd {link} {fixture}" in message

    def test_linked_worktree_cwd_spelled_with_dot_dot_components_gets_the_shell_only_announce(
        self, isolated_home, linked_worktree
    ):
        """Same expected difference as the symlink case: the hook compares the file's raw --cwd,
        while resume-context-same-session.sh canonicalizes it and loads it
        (see test_cwd_spelled_with_dot_dot_components_still_loads)."""
        dotted = f"{linked_worktree.resolve()}/../{linked_worktree.name}"
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", dotted)
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
        assert f"its resume command targets `{dotted}`, not this worktree" in message
        assert f"resume-context --cwd {dotted} {fixture}" in message

    def test_main_checkout_with_no_cwd_advertises_slash_form_in_both_channels(self, isolated_home, git_repo):
        fixture = _write_continuity_file(isolated_home, ".claude/briefs/example-task.md", None, kind="brief")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert code_span_command(message) == "/resume-context example-task.md"
        for channel in (message, context):
            assert f"resume-context {fixture}" not in channel
            assert "--cwd" not in channel

    def test_file_name_with_dots_and_digits_is_advertised_whole(self, isolated_home, git_repo):
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/v2.1-step-3.4-handoff.md", None)
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        assert "/resume-context v2.1-step-3.4-handoff.md" in payload["systemMessage"]
        assert "/resume-context v2.1-step-3.4-handoff.md" in payload["hookSpecificOutput"]["additionalContext"]

    def test_linked_worktree_with_no_cwd_in_file_omits_slash_form_and_gives_reason(
        self, isolated_home, linked_worktree
    ):
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", None)
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        payload = json.loads(result.stdout)
        for channel in (payload["systemMessage"], payload["hookSpecificOutput"]["additionalContext"]):
            assert f"resume-context --cwd {linked_worktree} {fixture}" in channel
            assert "/resume-context example-handoff.md" not in channel
        message = payload["systemMessage"]
        assert "/resume-context cannot confirm this file belongs to this worktree" in message
        assert f"because its resume command does not name this worktree (`{linked_worktree}`) as an absolute --cwd." in message

    def test_linked_worktree_with_other_cwd_in_file_omits_slash_form(
        self, isolated_home, linked_worktree, tmp_path
    ):
        elsewhere = tmp_path / "some-other-worktree"
        elsewhere.mkdir()
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", str(elsewhere))
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
            assert f"resume-context --cwd {elsewhere} {fixture}" in channel
        assert f"because its resume command targets `{elsewhere}`, not this worktree (`{linked_worktree}`)." in message
        _assert_context_labels_the_cwd_as_file_sourced(context)
        assert f"because its resume command targets `{elsewhere}`, not this worktree (`{linked_worktree}`)" in context

    @pytest.mark.parametrize(
        "unusable_cwd", ["~/some-worktree", "../some-worktree", "."], ids=["tilde", "relative-parent", "dot"]
    )
    def test_linked_worktree_with_unreadable_cwd_in_file_omits_slash_form_and_states_the_missing_worktree(
        self, isolated_home, linked_worktree, unusable_cwd
    ):
        """An unreadable --cwd is not a "different" one: the reason states only
        that the file does not name this worktree as an absolute --cwd."""
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", unusable_cwd)
        result = _run_hook_raw(
            ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(linked_worktree)), home=isolated_home
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert f"resume-context --cwd {linked_worktree} {fixture}" in channel
            assert "/resume-context example-handoff.md" not in channel
        assert f"because its resume command does not name this worktree (`{linked_worktree}`) as an absolute --cwd." in message

    def test_main_checkout_with_cwd_in_file_advertises_the_file_target_as_the_cwd(
        self, isolated_home, git_repo, tmp_path
    ):
        elsewhere = tmp_path / "some-worktree"
        elsewhere.mkdir()
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", str(elsewhere))
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert f"resume-context --cwd {elsewhere} {fixture}" in channel
            assert "/resume-context example-handoff.md" not in channel
            assert "from `" not in channel
        assert "/resume-context cannot confirm this file belongs to this directory" in message
        assert "belongs to this worktree" not in message
        assert f"because its resume command targets `{elsewhere}`, and this session is not in a linked worktree." in message
        _assert_context_labels_the_cwd_as_file_sourced(context)
        assert f"because its resume command targets `{elsewhere}`, and this session is not in a linked worktree" in context

    @pytest.mark.parametrize(
        "unusable_cwd",
        ["~/some-worktree", "../some-worktree", "."],
        ids=["tilde", "relative-parent", "dot"],
    )
    def test_main_checkout_with_unreadable_cwd_in_file_says_so_without_cwd_text(
        self, isolated_home, git_repo, unusable_cwd
    ):
        """A file whose --cwd cannot be read must not load into a main checkout, and must
        not be described as naming no directory."""
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", unusable_cwd)
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert f"resume-context {fixture}" in channel
            assert "/resume-context example-handoff.md" not in channel
            assert "--cwd" not in channel
        assert (
            "because its resume target could not be resolved to one directory, "
            "and this session is not in a linked worktree."
        ) in message
        assert "different" not in message

    def test_main_checkout_with_cwd_prose_beside_a_bare_resume_command_gives_a_reason_that_holds(
        self, isolated_home, git_repo
    ):
        """`--cwd` text elsewhere in the file reads as an unresolved target, though the
        resume command names no directory, so the reason must not claim that it does."""
        fixture = isolated_home / ".claude/handoffs/example-handoff.md"
        fixture.parent.mkdir(parents=True, exist_ok=True)
        fixture.write_text(f"Run `yarn --cwd pkg build` first.\n## §7 Resume command\n`resume-context {fixture}`\n")
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
            assert "--cwd" not in channel
        assert "because its resume target could not be resolved to one directory" in message
        assert "names a directory" not in message

    @pytest.mark.parametrize(
        "hostile_cwd",
        ["/elsewhere;touch-pwned", "/elsewhere$(touch-pwned)", "-elsewhere"],
        ids=["semicolon", "command-substitution", "leading-dash"],
    )
    @pytest.mark.parametrize("session", ["linked", "main"])
    def test_file_cwd_with_shell_metacharacters_is_never_interpolated(
        self, isolated_home, git_repo, linked_worktree, session, hostile_cwd
    ):
        """Deny twin of test_file_cwd_matching_the_allowlist_is_interpolated: the
        file's --cwd text reaches neither channel, and the announce falls back to
        the unresolved-target reason for the session kind."""
        session_cwd = linked_worktree if session == "linked" else git_repo
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", hostile_cwd)
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture), cwd=str(session_cwd)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert "touch-pwned" not in result.stdout
        assert "/elsewhere" not in result.stdout
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
            expected_command = (
                f"resume-context --cwd {linked_worktree} {fixture}" if session == "linked" else f"resume-context {fixture}"
            )
            assert expected_command in channel
        assert _SESSION_MEASURED_PROVENANCE in context

    @pytest.mark.parametrize(
        "wrongly_usable_target",
        ["/elsewhere;touch-pwned", "/elsewhere\ntouch-pwned", "/elsewhere`touch-pwned"],
        ids=["semicolon", "embedded-newline", "backtick"],
    )
    @pytest.mark.parametrize("session", ["linked", "main"])
    def test_file_cwd_the_helper_wrongly_reports_usable_is_still_not_interpolated(
        self, isolated_home, git_repo, linked_worktree, tmp_path, session, wrongly_usable_target
    ):
        """Seam: the hook's re-check of the helper's answer through `_lib_passes_path_char_allowlist`.
        A stand-in _lib_continuity_file_resume_cwd that returns a path with a byte outside the
        allowlist (a future helper regression) must not reach either channel, and the message stays well formed.
        The newline sits inside the value, because `$(...)` strips a trailing one before the
        gate and a stripped value would be usable. The byte-class matrix is owned by
        `test_single_byte_sweep_accepts_exactly_the_allowlisted_class` in test_lib_path_char_allowlist.py."""
        session_cwd = linked_worktree if session == "linked" else git_repo
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", None)
        result = _run_hook_with_stubbed_file_target(
            tmp_path, isolated_home, fixture, session_cwd, wrongly_usable_target
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        expected_command = (
            f"resume-context --cwd {linked_worktree} {fixture}" if session == "linked" else f"resume-context {fixture}"
        )
        assert code_span_command(message) == expected_command
        assert "touch-pwned" not in result.stdout
        assert "/resume-context example-handoff.md" not in message
        expected_reason = (
            f"does not name this worktree (`{linked_worktree}`) as an absolute --cwd"
            if session == "linked"
            else "its resume target could not be resolved to one directory"
        )
        assert expected_reason in message
        assert _SESSION_MEASURED_PROVENANCE in context

    @pytest.mark.parametrize("session", ["linked", "main"])
    def test_file_cwd_matching_the_allowlist_is_interpolated(
        self, isolated_home, git_repo, linked_worktree, tmp_path, session
    ):
        """Allow twin of the two tests above: the same stand-in helper returning an
        allowlist-clean path gets it interpolated as the --cwd, so the deny tests
        cannot pass because the stand-in never reaches the announce."""
        session_cwd = linked_worktree if session == "linked" else git_repo
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", None)
        result = _run_hook_with_stubbed_file_target(
            tmp_path, isolated_home, fixture, session_cwd, "/elsewhere/clean-target"
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert f"resume-context --cwd /elsewhere/clean-target {fixture}" in channel
        _assert_context_labels_the_cwd_as_file_sourced(context)

    def test_directory_outside_any_repository_loads_a_file_naming_no_cwd(self, isolated_home, tmp_path):
        """Accepted residual of reading git's exit 128 as "not linked": a directory git
        cannot open advertises /resume-context for a file that names no --cwd."""
        non_repo = tmp_path / "not-a-repo"
        non_repo.mkdir()
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", None)
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(non_repo)),
            home=isolated_home,
            extra_env={"GIT_CEILING_DIRECTORIES": str(tmp_path)},
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert "/resume-context example-handoff.md" in message
        assert "/resume-context example-handoff.md" in context

    @pytest.mark.parametrize("arm_id", list(_ANNOUNCE_ARMS))
    def test_each_announce_arm_owns_its_lead_route_reason_and_provenance_wording(
        self, isolated_home, git_repo, linked_worktree, tmp_path, arm_id
    ):
        """The one owner of every announce outcome's wording: the other announce tests assert what their
        scenario guards, not these strings."""
        arm = _ANNOUNCE_ARMS[arm_id]
        elsewhere = tmp_path / "some-other-directory"
        elsewhere.mkdir()
        is_brief = arm.kind == "brief"
        rel_path = arm.relative_path or (
            ".claude/briefs/example-task.md" if is_brief else ".claude/handoffs/example-handoff.md"
        )
        file_cwd = {
            None: None,
            "this-worktree": str(linked_worktree.resolve()),
            "elsewhere": str(elsewhere),
            "unusable": "~/some-worktree",
        }[arm.file_cwd]
        fixture = _write_continuity_file(isolated_home, rel_path, file_cwd, kind=arm.kind)
        payload = write_input(str(fixture))
        if arm.session != "unknown":
            payload = write_input(str(fixture), cwd=str(linked_worktree if arm.session == "linked" else git_repo))
        result = _run_hook_raw(ANNOUNCE_HOOK, payload, home=isolated_home)
        assert result.returncode == 0
        assert _resume_context_stub_invocations(isolated_home) == ""
        message, context = _both_channels(result)

        values = {"name": fixture.name, "root": linked_worktree, "elsewhere": elsewhere, "fixture": fixture}
        expected_command = arm.command.format(**values)
        reason = arm.reason.format(**values)
        is_load = arm.command.startswith("/resume-context ")
        scope = "this worktree" if arm.session == "linked" else "this directory"
        if not is_load:
            lead, route = _SHELL_LEAD.format(reason=reason), (
                f"outside this session: the engineer runs `{expected_command}` "
                f"in a new terminal on the machine that holds this file. {reason}"
            )
        elif is_brief:
            lead = _BRIEF_LOAD_LEAD.format(scope=scope, reason=reason)
            route = (
                f"in a session in {scope}: the engineer starts a new claude session there, or runs /clear in this one "
                f"once its work is done, then runs `{expected_command}`, because {reason}."
            )
        else:
            lead = _HANDOFF_LOAD_LEAD.format(reason=reason)
            route = f"in this session: the engineer runs /clear, then `{expected_command}`, because {reason}."

        assert code_span_command(message) == expected_command
        assert message.split("\n")[0] == lead
        assert context == (
            f"The resume route for the continuity file just written is {route} {arm.provenance} -- {_RELAY_INSTRUCTION}"
        )
        for channel in (message, context):
            assert "``" not in channel
        if is_load:
            for channel in (message, context):
                assert str(fixture) not in channel
                assert "--cwd" not in channel
                assert _SESSION_MEASURED_PROVENANCE not in channel
            if is_brief:
                first_line = message.split("\n")[0]
                assert first_line.index("Start a new claude session there") < first_line.index("/clear")
                assert "Run /clear, then:" not in message
                assert "Run /clear, then:" not in context
            else:
                assert "new claude session" not in message
                assert "new claude session" not in context

    def test_unknown_session_state_gives_bare_command_and_could_not_be_determined_reason(
        self, isolated_home, linked_worktree, tmp_path
    ):
        """A git call failing mid-sequence is neither linked nor not-linked, so
        the hook must not claim the file names a different --cwd."""
        stub_bin = tmp_path / "git-exits-1"
        _make_git_exiting_with_status(stub_bin, "--absolute-git-dir", 1)
        fixture = _write_continuity_file(
            isolated_home, ".claude/handoffs/example-handoff.md", str(linked_worktree.resolve())
        )
        result = _run_hook_raw(
            ANNOUNCE_HOOK,
            write_input(str(fixture), cwd=str(linked_worktree)),
            home=isolated_home,
            extra_env={"PATH": f"{stub_bin}:{os.environ['PATH']}", "REAL_GIT": shutil.which("git") or "git"},
        )
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert f"resume-context {fixture}" in channel
            assert "--cwd" not in channel
            assert "/resume-context example-handoff.md" not in channel
        assert "because this session's worktree could not be determined." in message
        assert "does not name this worktree" not in message

    def test_missing_payload_cwd_gives_could_not_be_determined_reason(self, isolated_home):
        fixture = _write_continuity_file(isolated_home, ".claude/handoffs/example-handoff.md", None)
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(str(fixture)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        for channel in (message, context):
            assert "/resume-context example-handoff.md" not in channel
            assert "--cwd" not in channel
        assert "because this session's worktree could not be determined." in message

    def _assert_slash_form_refused_for(self, isolated_home, git_repo, announced_path: str) -> None:
        """The hook is told `announced_path`; its verdict is load, yet only the shell route is announced."""
        result = _run_hook_raw(ANNOUNCE_HOOK, write_input(announced_path, cwd=str(git_repo)), home=isolated_home)
        assert result.returncode == 0
        message, context = _both_channels(result)
        assert code_span_command(message) == f"resume-context {announced_path}"
        assert message.split("\n")[0] == _SHELL_LEAD.format(reason=_REFUSED_NAME_REASON)
        assert f"`resume-context {announced_path}`" in context
        assert "`/resume-context" not in context
        assert _resume_context_stub_invocations(isolated_home) == ""

    @pytest.mark.parametrize(
        ("written_rel_path", "announced_rel_path"),
        [
            pytest.param(".claude/handoffs/-handoff.md", None, id="leading-dash"),
            pytest.param(".claude/briefs/-task.md", None, id="leading-dash-brief"),
            pytest.param(".claude/handoffs/nested/example-handoff.md", None, id="nested-path"),
            pytest.param(
                ".claude/handoffs/example-handoff.md",
                ".claude/handoffs/../handoffs/example-handoff.md",
                id="dot-dot-spelling",
            ),
            pytest.param(
                ".claude/handoffs/example-handoff.md",
                ".claude/handoffs//example-handoff.md",
                id="double-slash-spelling",
            ),
            pytest.param(
                ".claude/handoffs/example-handoff.md",
                ".claude/handoffs/./example-handoff.md",
                id="dot-segment-spelling",
            ),
        ],
    )
    def test_file_name_the_slash_form_refuses_falls_back_to_the_shell_route(
        self, isolated_home, git_repo, written_rel_path, announced_rel_path
    ):
        """The slash script refuses a leading dash and lists only _lib_is_pending_continuity_file paths, so a name
        the slash form refuses gets the shell route even though the file's verdict is load.
        A spelling row announces a non-literal spelling of the written, pending file: the hook must test the
        spelled FILE_PATH, not a normalized one.
        The pending-path matrix is in test_lib_resume_context.py."""
        written = _write_continuity_file(isolated_home, written_rel_path, None)
        announced_path = str(written) if announced_rel_path is None else f"{isolated_home}/{announced_rel_path}"
        self._assert_slash_form_refused_for(isolated_home, git_repo, announced_path)

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

    @pytest.mark.parametrize(
        "malicious_name",
        ["notes\n\nSENTINEL-INJECT\n\nx-handoff.md", "notes`SENTINEL-INJECT-x-handoff.md"],
        ids=["newline", "backtick"],
    )
    def test_file_path_with_a_byte_outside_the_allowlist_emits_nothing(self, isolated_home, malicious_name):
        """Seam: the FILE_PATH call to `_lib_passes_path_char_allowlist`. Bash `case` globs match
        across embedded newlines, so a newline path clears the continuity-path glob and
        must be caught by the allowlist gate instead -- the adversarial case that would
        otherwise leak an injected sentinel into model-visible additionalContext. A
        backtick would close the command's code span. The byte-class matrix is owned by
        `test_single_byte_sweep_accepts_exactly_the_allowlisted_class` in test_lib_path_char_allowlist.py."""
        handoffs_dir = isolated_home / ".claude" / "handoffs"
        handoffs_dir.mkdir(parents=True)
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
        assert code_span_command(message) == f"resume-context {stripped_path}"
        additional_context = payload["hookSpecificOutput"]["additionalContext"]
        assert "\x00" not in message
        assert "\x00" not in additional_context
        assert "\n" not in additional_context

    @pytest.mark.parametrize(
        "malicious_root",
        ["linked\n\nSENTINEL-INJECT\n\nwt", "linked`SENTINEL-INJECT-wt"],
        ids=["newline", "backtick"],
    )
    def test_worktree_root_with_a_byte_outside_the_allowlist_falls_back_to_bare_command(
        self, isolated_home, tmp_path, malicious_root
    ):
        """Seam: the WORKTREE_ROOT allowlist call, the counterpart to
        test_file_path_with_a_byte_outside_the_allowlist_emits_nothing: a worktree root
        holding a newline or a backtick fails the allowlist, so the session
        state is unknown, --cwd and the slash form are dropped, the message stays
        well formed, and none of the root's text, including any injected sentinel,
        reaches the output. The byte-class matrix is owned by
        `test_single_byte_sweep_accepts_exactly_the_allowlisted_class` in test_lib_path_char_allowlist.py.

        Uses a PATH-stubbed git because the CANDIDATE_ROOT call site never validates
        the path is a real directory, so it is platform-independent.
        A real `git worktree add` with a newline in the path depends on the local git
        accepting such paths, so the stub avoids that dependency."""
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
        message, context = _both_channels(result)
        assert code_span_command(message) == f"resume-context {fixture}"
        for channel in (message, context):
            assert "--cwd" not in channel
            assert f"resume-context {fixture}" in channel
            assert "/resume-context example-handoff.md" not in channel
        assert "because this session's worktree could not be determined." in message
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
