"""Tests for claude/.claude/scripts/marker.sh."""
from __future__ import annotations

import os
import subprocess

import pytest
from helpers import SCRIPTS_DIR

MARKER_SCRIPT = SCRIPTS_DIR / "marker.sh"


def _run(args: list[str], cwd, home) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(MARKER_SCRIPT)] + args,
        cwd=cwd,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


class TestMarkerScriptSessionMissing:
    """When the session file ($HOME/.claude/sessions/$PPID) does not exist,
    every write/activate/deactivate subcommand must exit 2 and write nothing.
    This guards against the silent-empty-suffix regression: without explicit
    || exit 2 on the command-substitution call sites, the return 2 inside
    _resolve_session_id() only sets the subshell exit status — the parent
    continues and writes a malformed marker with an empty session-id
    suffix."""

    @pytest.mark.parametrize(
        "args",
        [
            ["write", "code-review"],
            ["write", "skill-review"],
            ["write", "plan-review"],
            ["write", "ready-for-review"],
            ["activate", "plan-review"],
            ["activate", "ready-for-review"],
            ["activate", "respond-pr"],
            ["activate", "memory-skill"],
            ["deactivate", "plan-review"],
            ["deactivate", "ready-for-review"],
            ["deactivate", "respond-pr"],
            ["deactivate", "memory-skill"],
        ],
    )
    def test_exits_2_when_session_file_missing(self, isolated_home, git_repo, args):
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"marker.sh {' '.join(args)} should exit 2 when session file is absent, "
            f"got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_no_marker_written_when_session_file_missing(self, isolated_home, git_repo):
        _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        marker_dir = isolated_home / ".claude" / "review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], (
            f"marker.sh wrote a stray marker when session file was absent: {stray}"
        )


class TestMarkerScriptHappyPath:
    """Smoke-test that each subcommand writes/removes the expected file when
    the session file is present."""

    def _seed_session(self, home):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = "test-session-abc"
        (sessions_dir / str(os.getpid())).write_text(sid)
        return sid

    def test_write_code_review_creates_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")

    def test_activate_creates_active_marker_with_pid(self, isolated_home, git_repo):
        """activate must write the Claude session PID to the active.d file body
        so hooks can check liveness with kill -0."""
        sid = self._seed_session(isolated_home)
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists()
        content = active_file.read_text().strip()
        assert content.isdigit(), (
            f"activate must write a numeric PID to the active marker, got: {content!r}"
        )
        stored_pid = int(content)
        assert stored_pid > 0, f"stored PID must be positive, got: {stored_pid}"

    def test_activate_ready_for_review_writes_pid(self, isolated_home, git_repo):
        """activate ready-for-review writes the Claude session PID (same as
        other skills) — hook now uses PID liveness, not epoch timestamp."""
        sid = self._seed_session(isolated_home)
        result = _run(["activate", "ready-for-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".ready-for-review-active.d" / sid
        assert active_file.exists()
        content = active_file.read_text().strip()
        assert content.isdigit(), (
            f"activate ready-for-review must write a numeric PID, got: {content!r}"
        )

    def test_deactivate_removes_active_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / sid).touch()
        result = _run(["deactivate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not (active_dir / sid).exists()

    def test_activate_memory_skill_creates_active_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        result = _run(["activate", "memory-skill"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert active_file.exists()

    def test_deactivate_memory_skill_removes_active_marker(self, isolated_home, git_repo):
        sid = self._seed_session(isolated_home)
        active_dir = isolated_home / ".claude" / ".memory-skill-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / sid).touch()
        result = _run(["deactivate", "memory-skill"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not (active_dir / sid).exists()

    def test_help_exits_0(self, isolated_home, git_repo):
        result = _run(["--help"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0


class TestMarkerScriptClearStale:
    """Tests for `marker.sh clear-stale [--dry-run]`."""

    def _make_active_dir(self, home, skill):
        d = home / ".claude" / f".{skill}-active.d"
        d.mkdir(parents=True)
        return d

    def test_clear_stale_removes_dead_pid_entry(self, isolated_home, git_repo):
        """clear-stale evicts entries whose stored PID is dead."""
        d = self._make_active_dir(isolated_home, "plan-review")
        orphan = d / "orphan-session"
        orphan.write_text("99999999")
        result = _run(["clear-stale"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not orphan.exists(), "clear-stale must remove dead-PID marker"
        assert "evict" in result.stdout

    def test_clear_stale_keeps_alive_pid_entry(self, isolated_home, git_repo):
        """clear-stale preserves entries whose stored PID is alive."""
        d = self._make_active_dir(isolated_home, "respond-pr")
        alive = d / "live-session"
        alive.write_text(str(os.getpid()))
        result = _run(["clear-stale"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert alive.exists(), "clear-stale must not remove alive-PID marker"

    def test_clear_stale_dry_run_does_not_remove(self, isolated_home, git_repo):
        """--dry-run reports would-evict entries without removing them."""
        d = self._make_active_dir(isolated_home, "memory-skill")
        orphan = d / "dry-run-session"
        orphan.write_text("99999999")
        result = _run(["clear-stale", "--dry-run"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert orphan.exists(), "--dry-run must not remove markers"
        assert "dry-run" in result.stdout or "would evict" in result.stdout

    def test_clear_stale_no_active_dirs_exits_0(self, isolated_home, git_repo):
        """When no active.d directories exist, exits 0 with zero evictions."""
        result = _run(["clear-stale"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0
        assert "evicted 0" in result.stdout

    def test_clear_stale_invalid_extra_arg_exits_2(self, isolated_home, git_repo):
        result = _run(["clear-stale", "--unknown-flag"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2


class TestMarkerScriptEmptyStagedGuard:
    """Guard added to hash-based marker writes (code-review, skill-review):
    exit 2 when the staged diff for the relevant pathspec is empty but
    unstaged tracked changes exist — prevents recording an empty-hash marker
    before `git add` is run, which would cause a require-* hook mismatch at
    commit time."""

    def _seed_session(self, home):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = "test-session-guard"
        (sessions_dir / str(os.getpid())).write_text(sid)
        return sid

    # ── code-review (whole-tree pathspec) ─────────────────────────────────

    def test_code_review_staged_and_unstaged_writes_marker(
        self, isolated_home, git_repo
    ):
        """Guard must NOT fire when something is staged — even with unstaged
        changes alongside it."""
        self._seed_session(isolated_home)
        # Fixture has file.txt staged with "first\nsecond\n". Write an
        # additional working-tree change without staging it — index has a
        # staged change, working tree has a further unstaged change.
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def test_code_review_empty_staged_unstaged_tracked_exits_2(
        self, isolated_home, git_repo
    ):
        """Guard fires: staged diff is empty, unstaged tracked changes exist."""
        self._seed_session(isolated_home)
        # Unstage the fixture's pre-staged change, leaving it as an unstaged
        # tracked modification.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "git add" in result.stderr
        assert "/code-review" in result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"guard should not write a marker: {stray}"

    def test_code_review_empty_staged_no_unstaged_writes_marker(
        self, isolated_home, git_repo
    ):
        """Guard must NOT fire when staged is empty AND there are no unstaged
        changes — the review-of-nothing escape hatch must stay open."""
        self._seed_session(isolated_home)
        # Unstage then discard the fixture's change so both index and working
        # tree are clean. Order matters: reset the index first (HEAD → index),
        # then checkout to align the working tree with the now-clean index.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        subprocess.run(["git", "checkout", "--", "file.txt"], cwd=git_repo, check=True)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    # ── skill-review (path-scoped to SKILL.md) ────────────────────────────

    def _make_skill_md(self, repo):
        """Create a tracked SKILL.md inside the repo at the expected pathspec."""
        skill_dir = repo / "claude" / ".claude" / "skills" / "test-skill"
        skill_dir.mkdir(parents=True)
        skill_md = skill_dir / "SKILL.md"
        skill_md.write_text("# test skill\n")
        subprocess.run(["git", "add", str(skill_md)], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add test skill"],
            cwd=repo,
            check=True,
        )
        return skill_md

    def test_skill_review_out_of_scope_unstaged_does_not_fire(
        self, isolated_home, git_repo
    ):
        """Unstaged change outside the SKILL.md pathspec with nothing staged
        must NOT trigger the guard — the guard is pathspec-scoped."""
        self._seed_session(isolated_home)
        # file.txt is staged from the fixture; reset it so staged is empty for
        # the whole tree. The unstaged file.txt change is outside the SKILL.md
        # pathspec — guard must pass through.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def test_skill_review_unstaged_skill_md_exits_2(self, isolated_home, git_repo):
        """Guard fires when staged SKILL.md diff is empty but an unstaged
        SKILL.md change exists."""
        self._seed_session(isolated_home)
        skill_md = self._make_skill_md(git_repo)
        # Modify SKILL.md without staging it.
        skill_md.write_text("# test skill\nmodified\n")
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "git add" in result.stderr
        assert "/skill-review" in result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"guard should not write a marker: {stray}"

    def test_skill_review_staged_skill_md_writes_marker(self, isolated_home, git_repo):
        """Guard must NOT fire when SKILL.md change is staged."""
        self._seed_session(isolated_home)
        skill_md = self._make_skill_md(git_repo)
        skill_md.write_text("# test skill\nupdated\n")
        subprocess.run(["git", "add", str(skill_md)], cwd=git_repo, check=True)
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    # ── skill-review: agent-file coverage (claude/.claude/agents/*.md + plugins/*/agents/*.md) ──

    def _make_stowed_agent(self, repo):
        """Create a tracked agent file inside the repo at the expected pathspec."""
        agent_dir = repo / "claude" / ".claude" / "agents"
        agent_dir.mkdir(parents=True)
        agent_md = agent_dir / "test-agent.md"
        agent_md.write_text("---\nname: test-agent\ndescription: x\n---\n\nbody\n")
        subprocess.run(["git", "add", str(agent_md)], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add test agent"],
            cwd=repo,
            check=True,
        )
        return agent_md

    def _make_plugin_agent(self, repo):
        """Create a tracked plugin agent file at plugins/<name>/agents/<name>.md."""
        agent_dir = repo / "plugins" / "some-plugin" / "agents"
        agent_dir.mkdir(parents=True)
        agent_md = agent_dir / "test-agent.md"
        agent_md.write_text("---\nname: test-agent\ndescription: x\n---\n\nbody\n")
        subprocess.run(["git", "add", str(agent_md)], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add plugin agent"],
            cwd=repo,
            check=True,
        )
        return agent_md

    def test_skill_review_unstaged_stowed_agent_exits_2(self, isolated_home, git_repo):
        """Guard fires when staged diff is empty but an unstaged stowed-agent change exists."""
        self._seed_session(isolated_home)
        agent_md = self._make_stowed_agent(git_repo)
        agent_md.write_text("---\nname: test-agent\ndescription: x\n---\n\nmodified\n")
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "git add" in result.stderr
        assert "/skill-review" in result.stderr

    def test_skill_review_staged_stowed_agent_writes_marker(self, isolated_home, git_repo):
        """Guard must NOT fire when a stowed-agent change is staged; marker writes successfully."""
        self._seed_session(isolated_home)
        agent_md = self._make_stowed_agent(git_repo)
        agent_md.write_text("---\nname: test-agent\ndescription: x\n---\n\nupdated\n")
        subprocess.run(["git", "add", str(agent_md)], cwd=git_repo, check=True)
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def test_skill_review_staged_plugin_agent_writes_marker(self, isolated_home, git_repo):
        """Guard must NOT fire when a plugin-agent change is staged; marker writes successfully."""
        self._seed_session(isolated_home)
        agent_md = self._make_plugin_agent(git_repo)
        agent_md.write_text("---\nname: test-agent\ndescription: x\n---\n\nupdated\n")
        subprocess.run(["git", "add", str(agent_md)], cwd=git_repo, check=True)
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1


class TestMarkerScriptStalePidLookup:
    """Regression guard: `activate` must stamp the live Claude PID into the
    active-bypass marker even when ~/.claude/sessions/ holds stale entries
    from prior (crashed) sessions. The PID is resolved from the process
    ancestor walk, not by content-scanning the sessions directory, so stale
    files cannot mislead it."""

    def _seed_session(self, home, sid="test-session-stale"):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / str(os.getpid())).write_text(sid)
        return sid, sessions_dir

    def test_activate_ignores_stale_session_entry(self, isolated_home, git_repo):
        """A stale sessions/ entry whose filename sorts lexically before the
        live PID — what the old reverse-lookup directory scan would have
        picked first — must not end up in the active marker."""
        sid, sessions_dir = self._seed_session(isolated_home)
        # Leading-zero filename: capture-session-id.sh only ever writes bare
        # PIDs, so a "0"-prefixed name is never a real entry, and it sorts
        # lexically before every str(os.getpid()) (which never starts with
        # "0"). The pre-fix directory scan would have stamped this into the
        # marker; the ancestor walk never reads it.
        stale = sessions_dir / "08888888"
        stale.write_text(sid)
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists()
        content = active_file.read_text().strip()
        assert content == str(os.getpid()), (
            f"activate must stamp the live ancestor PID, not the stale "
            f"sessions entry; got {content!r}"
        )

    def test_write_arm_still_resolves_session_id(self, isolated_home, git_repo):
        """The refactored _resolve_session_id keeps its signature: a `write`
        arm (which needs only the session id) resolves and writes its
        marker with the correct session-id suffix."""
        sid, _ = self._seed_session(isolated_home)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")
