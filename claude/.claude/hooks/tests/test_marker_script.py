"""Tests for claude/.claude/scripts/marker.sh."""
from __future__ import annotations

import os
import re
import subprocess

import pytest
from conftest import _seed_session
from helpers import (
    CANARY_CONTENT,
    HOOKS_DIR,
    SCRIPTS_DIR,
    TRAVERSAL_SESSION_ID,
    bash_input,
    plant_traversal_canary,
    run_hook,
)

MARKER_SCRIPT = SCRIPTS_DIR / "marker.sh"


def _run(
    args: list[str], cwd, home, extra_env: dict | None = None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(MARKER_SCRIPT)] + args,
        cwd=cwd,
        env=env,
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
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], (
            f"marker.sh wrote a stray marker when session file was absent: {stray}"
        )


class TestMarkerScriptSessionIdValidation:
    """The session file's CONTENT — not just its presence — feeds every
    write/activate/deactivate path as a filesystem path component. A session
    file holding a value like '../canary' (e.g. corrupted, or a hostile
    subagent racing to write its own sessions/<pid> entry) must be rejected
    by the same _resolve_session_id() chokepoint that handles a missing
    session file, rather than flowing into `rm -f`/`>` against a path outside
    the marker/active directories."""

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
    def test_exits_2_when_session_id_is_path_escaping(self, isolated_home, git_repo, args):
        _seed_session(isolated_home, TRAVERSAL_SESSION_ID)
        result = _run(args, cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"marker.sh {' '.join(args)} should exit 2 for a path-escaping "
            f"session id, got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_no_marker_written_for_path_escaping_session_id(self, isolated_home, git_repo):
        _seed_session(isolated_home, TRAVERSAL_SESSION_ID)
        canary = plant_traversal_canary(isolated_home)

        _run(["write", "code-review"], cwd=git_repo, home=isolated_home)

        marker_dir = isolated_home / ".claude" / "code-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], (
            f"marker.sh wrote a stray marker for a path-escaping session id: {stray}"
        )
        assert canary.read_text() == CANARY_CONTENT, (
            "a path-escaping session id must not let 'write' touch a file "
            "outside the markers directory"
        )

    def test_no_active_marker_written_for_path_escaping_session_id(
        self, isolated_home, git_repo
    ):
        _seed_session(isolated_home, TRAVERSAL_SESSION_ID)
        canary = plant_traversal_canary(isolated_home)

        _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)

        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        stray = list(active_dir.iterdir()) if active_dir.exists() else []
        assert stray == [], (
            f"marker.sh wrote a stray active marker for a path-escaping session id: {stray}"
        )
        assert canary.read_text() == CANARY_CONTENT


class TestMarkerScriptHappyPath:
    """Smoke-test that each subcommand writes/removes the expected file when
    the session file is present."""

    SID = "test-session-abc"

    def test_write_code_review_creates_marker(self, isolated_home, git_repo):
        sid = self.SID
        _seed_session(isolated_home, sid)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")

    def test_write_plan_review_stores_hash_not_literal(self, isolated_home, git_repo):
        """write plan-review must store _lib_active_plan_hash's output (a
        sha256 hex digest of the active plan set), not the legacy literal
        'reviewed' existence-only sentinel."""
        _seed_session(isolated_home, self.SID)
        plans_dir = git_repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        (plans_dir / "p.md").write_text("# plan\n")
        result = _run(["write", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "plan-review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        content = files[0].read_text().strip()
        assert content != "reviewed"
        assert re.fullmatch(r"[0-9a-f]{64}", content), (
            f"expected a sha256 hex digest, got {content!r}"
        )

    @pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file permission bits")
    def test_write_plan_review_aborts_without_clobbering_marker(self, isolated_home, git_repo):
        """An unhashable active plan must abort the write with a non-zero
        status naming the file -- and must leave any pre-existing marker
        byte-identical. Redirecting the helper's output straight into the
        marker path would truncate it before the failure surfaced, so a
        failed attempt would destroy a good marker as a side effect."""
        sid = self.SID
        _seed_session(isolated_home, sid)
        plans_dir = git_repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True)
        plan = plans_dir / "p.md"
        plan.write_text("# plan\n")

        assert _run(["write", "plan-review"], cwd=git_repo, home=isolated_home).returncode == 0
        marker = isolated_home / ".claude" / "plan-review-markers" / next(
            f.name for f in (isolated_home / ".claude" / "plan-review-markers").iterdir()
        )
        good_content = marker.read_text()
        assert marker.name.endswith(f".{sid}")

        plan.chmod(0o000)
        try:
            result = _run(["write", "plan-review"], cwd=git_repo, home=isolated_home)
        finally:
            plan.chmod(0o644)

        assert result.returncode == 2, result.stderr
        assert "p.md" in result.stderr, (
            f"stderr must name the offending plan file, got {result.stderr!r}"
        )
        assert marker.read_text() == good_content, (
            "a failed write must not truncate or alter the existing marker"
        )

    def test_activate_creates_active_marker_with_pid(self, isolated_home, git_repo):
        """activate must write the Claude session PID to the active.d file body
        so hooks can check liveness with kill -0."""
        sid = self.SID
        _seed_session(isolated_home, sid)
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
        sid = self.SID
        _seed_session(isolated_home, sid)
        result = _run(["activate", "ready-for-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".ready-for-review-active.d" / sid
        assert active_file.exists()
        content = active_file.read_text().strip()
        assert content.isdigit(), (
            f"activate ready-for-review must write a numeric PID, got: {content!r}"
        )

    def test_deactivate_removes_active_marker(self, isolated_home, git_repo):
        sid = self.SID
        _seed_session(isolated_home, sid)
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / sid).touch()
        result = _run(["deactivate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        assert not (active_dir / sid).exists()

    def test_activate_memory_skill_creates_active_marker(self, isolated_home, git_repo):
        sid = self.SID
        _seed_session(isolated_home, sid)
        result = _run(["activate", "memory-skill"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".memory-skill-active.d" / sid
        assert active_file.exists()

    def test_deactivate_memory_skill_removes_active_marker(self, isolated_home, git_repo):
        sid = self.SID
        _seed_session(isolated_home, sid)
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

    SID = "test-session-guard"

    # ── code-review (whole-tree pathspec) ─────────────────────────────────

    def test_code_review_staged_and_unstaged_writes_marker(
        self, isolated_home, git_repo
    ):
        """Guard must NOT fire when something is staged — even with unstaged
        changes alongside it."""
        _seed_session(isolated_home, self.SID)
        # Fixture has file.txt staged with "first\nsecond\n". Write an
        # additional working-tree change without staging it — index has a
        # staged change, working tree has a further unstaged change.
        (git_repo / "file.txt").write_text("first\nsecond\nthird\n")
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def test_code_review_empty_staged_unstaged_tracked_exits_2(
        self, isolated_home, git_repo
    ):
        """Guard fires: staged diff is empty, unstaged tracked changes exist."""
        _seed_session(isolated_home, self.SID)
        # Unstage the fixture's pre-staged change, leaving it as an unstaged
        # tracked modification.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "git add" in result.stderr
        assert "/code-review" in result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"guard should not write a marker: {stray}"

    def test_code_review_empty_staged_no_unstaged_writes_marker(
        self, isolated_home, git_repo
    ):
        """Guard must NOT fire when staged is empty AND there are no unstaged
        changes — the review-of-nothing escape hatch must stay open."""
        _seed_session(isolated_home, self.SID)
        # Unstage then discard the fixture's change so both index and working
        # tree are clean. Order matters: reset the index first (HEAD → index),
        # then checkout to align the working tree with the now-clean index.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        subprocess.run(["git", "checkout", "--", "file.txt"], cwd=git_repo, check=True)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
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
        _seed_session(isolated_home, self.SID)
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
        _seed_session(isolated_home, self.SID)
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
        _seed_session(isolated_home, self.SID)
        skill_md = self._make_skill_md(git_repo)
        skill_md.write_text("# test skill\nupdated\n")
        subprocess.run(["git", "add", str(skill_md)], cwd=git_repo, check=True)
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def _make_routing_md_like_file(self, repo, skill_name="other-skill"):
        """Create a tracked ROUTING.md outside plan-review — same filename,
        different skill directory, so it is out of scope for the hardcoded
        `claude/.claude/skills/plan-review/ROUTING.md` pathspec."""
        skill_dir = repo / "claude" / ".claude" / "skills" / skill_name
        skill_dir.mkdir(parents=True)
        routing_md = skill_dir / "ROUTING.md"
        routing_md.write_text("# not the gated ROUTING.md\n")
        subprocess.run(["git", "add", str(routing_md)], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add out-of-scope ROUTING.md"],
            cwd=repo,
            check=True,
        )
        return routing_md

    def _make_plan_review_routing_md(self, repo):
        """Create a tracked plan-review/ROUTING.md — the exact hardcoded pathspec."""
        routing_dir = repo / "claude" / ".claude" / "skills" / "plan-review"
        routing_dir.mkdir(parents=True, exist_ok=True)
        routing_md = routing_dir / "ROUTING.md"
        routing_md.write_text("# test routing\n")
        subprocess.run(["git", "add", str(routing_md)], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add test routing"],
            cwd=repo,
            check=True,
        )
        return routing_md

    def test_skill_review_out_of_scope_routing_md_like_file_does_not_fire(
        self, isolated_home, git_repo
    ):
        """An unstaged change to a ROUTING.md-*named* file outside
        plan-review is out of scope for the hardcoded
        `claude/.claude/skills/plan-review/ROUTING.md` pathspec — the guard
        must not fire, proving the pathspec is the exact path, not a
        generic `**/ROUTING.md` glob."""
        _seed_session(isolated_home, self.SID)
        # file.txt is staged from the fixture; reset it so staged is empty for
        # the whole tree, matching the sibling out-of-scope test's setup.
        subprocess.run(["git", "reset", "HEAD", "--", "file.txt"], cwd=git_repo, check=True)
        routing_md = self._make_routing_md_like_file(git_repo)
        # Unstaged, out-of-scope modification.
        routing_md.write_text("# not the gated ROUTING.md\nmodified\n")
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        assert len(list(marker_dir.iterdir())) == 1

    def test_skill_review_unstaged_routing_md_exits_2(self, isolated_home, git_repo):
        """Guard fires when the staged plan-review/ROUTING.md diff is empty
        but an unstaged ROUTING.md change exists."""
        _seed_session(isolated_home, self.SID)
        routing_md = self._make_plan_review_routing_md(git_repo)
        # Modify ROUTING.md without staging it.
        routing_md.write_text("# test routing\nmodified\n")
        result = _run(["write", "skill-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2
        assert "git add" in result.stderr
        assert "/skill-review" in result.stderr
        marker_dir = isolated_home / ".claude" / "skill-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"guard should not write a marker: {stray}"


class TestMarkerScriptStalePidLookup:
    """Regression guard: `activate` must stamp the live Claude PID into the
    active-bypass marker even when ~/.claude/sessions/ holds stale entries
    from prior (crashed) sessions. The PID is resolved from the process
    ancestor walk, not by content-scanning the sessions directory, so stale
    files cannot mislead it."""

    SID = "test-session-stale"

    def test_activate_ignores_stale_session_entry(self, isolated_home, git_repo):
        """A stale sessions/ entry whose filename sorts lexically before the
        live PID — what the old reverse-lookup directory scan would have
        picked first — must not end up in the active marker."""
        sid = self.SID
        _seed_session(isolated_home, sid)
        sessions_dir = isolated_home / ".claude" / "sessions"
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
        sid = self.SID
        _seed_session(isolated_home, sid)
        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")


class TestMarkerDirectoryNamingConvention:
    """`write <skill>` must land its marker in ~/.claude/<skill>-markers/.

    A marker directory whose name does not derive mechanically from the skill
    name is unguessable: a session debugging a blocked commit infers the
    directory from the skill it just ran, looks in a path that does not exist,
    and reads the absent directory as a failed marker write.

    These assert on the directory marker.sh actually creates, not on the path
    literal in its source. Source-scanning would pass on a shadowed duplicate
    `case` arm (bash dispatches on the first match; a later arm carrying the
    right literal would satisfy a text scan while the live arm stayed broken)
    and would fail on a refactor to a shared "${SKILL}-markers" expansion that
    preserves the invariant.
    """

    WRITE_SKILLS = ("code-review", "skill-review", "plan-review", "ready-for-review")

    SID = "test-session-naming"

    @pytest.mark.parametrize("skill", WRITE_SKILLS)
    def test_write_lands_in_skill_derived_directory(
        self, skill, isolated_home, git_repo
    ):
        sid = self.SID
        _seed_session(isolated_home, sid)
        # plan-review hashes the active plan set; seeding one unconditionally
        # gives every arm the same preconditions so the loop stays uniform.
        plans_dir = git_repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        (plans_dir / "p.md").write_text("# plan\n")

        result = _run(["write", skill], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr

        expected_dir = isolated_home / ".claude" / f"{skill}-markers"
        assert expected_dir.is_dir(), (
            f"`marker.sh write {skill}` must create ~/.claude/{skill}-markers/ "
            f"so a session can derive the directory from the skill name"
        )
        written = [f.name for f in expected_dir.iterdir()]
        assert len(written) == 1 and written[0].endswith(f".{sid}"), (
            f"expected one marker keyed <repo-hash>.{sid} in "
            f"{expected_dir.name}/, found {written}"
        )

    @pytest.mark.parametrize("skill", WRITE_SKILLS)
    def test_write_touches_no_other_marker_directory(
        self, skill, isolated_home, git_repo
    ):
        """The inverse guard: an arm that writes some other skill's directory
        (a copy-paste slip between adjacent `case` arms) would leave the
        assertion above green, since that arm's own directory is created too."""
        _seed_session(isolated_home, self.SID)
        plans_dir = git_repo / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        (plans_dir / "p.md").write_text("# plan\n")

        assert (
            _run(["write", skill], cwd=git_repo, home=isolated_home).returncode == 0
        )

        # isolated_home pre-creates code-review-markers/, so presence alone
        # proves nothing — a stray is a *non-empty* foreign marker directory.
        strays = sorted(
            d.name
            for d in (isolated_home / ".claude").iterdir()
            if d.is_dir()
            and d.name.endswith("-markers")
            and d.name != f"{skill}-markers"
            and any(d.iterdir())
        )
        assert strays == [], (
            f"`marker.sh write {skill}` also wrote into {strays}; each write "
            f"arm must touch only its own marker directory"
        )

    def test_write_roster_is_closed_and_self_reported(self, isolated_home, git_repo):
        """Pin WRITE_SKILLS by execution rather than by reading marker.sh's
        source. Rejecting an unlisted skill proves the roster is closed — the
        parametrized guards above only prove each listed skill is accepted,
        which stays green if the dispatch grows an extra arm. Reading the
        roster back off the rejection message keeps the guards iterating the
        same set the script advertises at runtime."""
        result = _run(["write", "not-a-real-skill"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            "`marker.sh write` must reject a skill outside its roster; got "
            f"exit {result.returncode}"
        )

        advertised = re.search(r"'write' supports: (.+?)\s*$", result.stderr, re.M)
        assert advertised, (
            f"rejection message no longer advertises the write roster: {result.stderr!r}"
        )
        assert tuple(s.strip() for s in advertised.group(1).split(",")) == self.WRITE_SKILLS, (
            f"marker.sh advertises write skills {advertised.group(1)!r}; the "
            f"convention guards iterate {self.WRITE_SKILLS} — reconcile the two"
        )

        strays = [
            d.name
            for d in (isolated_home / ".claude").iterdir()
            if d.is_dir() and d.name.endswith("-markers") and any(d.iterdir())
        ]
        assert strays == [], f"a rejected write still created markers in {strays}"


class TestMarkerWriteSatisfiesTheGate:
    """marker.sh (write side) and require-code-review.sh (read side) each
    build the marker path independently. Prove they agree by execution rather
    than by inspection: the rest of the hook suite seeds markers through a
    Python-side path helper, which only shows that the helper and the hook
    agree with each other — both could drift from marker.sh in lockstep and
    stay green."""

    def test_write_code_review_marker_opens_the_commit_gate(
        self, isolated_home, git_repo
    ):
        sid = "test-session-roundtrip"
        _seed_session(isolated_home, sid)

        result = _run(["write", "code-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr

        assert (
            run_hook(
                HOOKS_DIR / "require-code-review.sh",
                bash_input("git commit -m roundtrip", session_id=sid),
                cwd=git_repo,
            )
            == "allow"
        )


class TestMarkerScriptHonorsConfigDir:
    """CLAUDE_CONFIG_DIR relocates every marker path marker.sh writes, not
    just $HOME/.claude -- closes the cross-account bypass this plan targets."""

    def test_write_code_review_lands_under_config_dir(
        self, isolated_home, git_repo, tmp_path
    ):
        config_dir = tmp_path / "custom-config-dir"
        sessions_dir = config_dir / "sessions"
        sessions_dir.mkdir(parents=True)
        sid = "test-session-config-dir"
        # Two-line format capture-session-id.sh writes: session id, then this
        # PID's TZ=UTC LC_ALL=C ps -o lstart= start time (see conftest.py's
        # _seed_session, which can't be reused here -- it targets the
        # standard $HOME/.claude/sessions path, not an arbitrary CONFIG_DIR).
        start_time = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(os.getpid())],
            env={**os.environ, "TZ": "UTC", "LC_ALL": "C"},
            capture_output=True,
            text=True,
            check=True,
        ).stdout.rstrip("\n")
        (sessions_dir / str(os.getpid())).write_text(f"{sid}\n{start_time}\n")

        result = _run(
            ["write", "code-review"],
            cwd=git_repo,
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": str(config_dir)},
        )
        assert result.returncode == 0, result.stderr

        marker_dir = config_dir / "code-review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")

        home_marker_dir = isolated_home / ".claude" / "code-review-markers"
        assert list(home_marker_dir.iterdir()) == [], (
            "marker.sh must not also write under $HOME/.claude when "
            "CLAUDE_CONFIG_DIR is set"
        )

    def test_write_aborts_when_config_dir_unresolvable(self, isolated_home, git_repo):
        """Fail closed (ledger row 11): a relative CLAUDE_CONFIG_DIR must
        abort the write rather than silently falling through to a
        root-anchored path."""
        result = _run(
            ["write", "code-review"],
            cwd=git_repo,
            home=isolated_home,
            extra_env={"CLAUDE_CONFIG_DIR": "relative/path"},
        )
        assert result.returncode == 2, result.stderr
        assert "config directory" in result.stderr
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"a resolver failure must not write a marker: {stray}"


class TestSessionStartTimeResolution:
    """_walk_session's PID-reuse guard: a sessions/<pid> entry is trusted
    only when its recorded start time matches the live process's current
    `ps -o lstart=` start time. Named invariant (see
    .claude/plans/retire-sessionend-destructors.md): any entry that doesn't
    satisfy this — mismatched, missing the second line, or empty — is
    untrusted and must fall through to the next ancestor exactly as if the
    file were absent, never resolve to a session id."""

    def _write_raw_session_file(self, home, content: str) -> None:
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        (sessions_dir / str(os.getpid())).write_text(content)

    def test_mismatched_start_time_does_not_resolve(self, isolated_home, git_repo):
        """A start time that doesn't match the live PID's actual lstart --
        what a reused PID's stale entry looks like -- must not authorize an
        activate."""
        self._write_raw_session_file(
            isolated_home, "test-session-mismatch\nMon Jan  1 00:00:00 1970\n"
        )
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"a mismatched start time must not resolve a session id, got "
            f"{result.returncode}. stderr: {result.stderr!r}"
        )
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        stray = list(active_dir.iterdir()) if active_dir.exists() else []
        assert stray == [], f"a mismatched-start-time entry must not write a marker: {stray}"

    def test_old_format_single_line_leftover_resolves_as_absent(
        self, isolated_home, git_repo
    ):
        """Every sessions/<pid> entry on disk at merge time is this shape:
        written by the pre-fix capture-session-id.sh, one line, no recorded
        start time. It must resolve exactly as if the file were absent, not
        crash and not false-match."""
        self._write_raw_session_file(isolated_home, "test-session-old-format\n")
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"an old-format single-line entry must resolve as absent, got "
            f"{result.returncode}. stderr: {result.stderr!r}"
        )

    @pytest.mark.parametrize(
        "content",
        [
            pytest.param("", id="empty_file"),
            pytest.param("\nMon Jan  1 00:00:00 1970\n", id="empty_first_line"),
        ],
    )
    def test_empty_session_or_empty_first_line_resolves_as_absent(
        self, isolated_home, git_repo, content
    ):
        self._write_raw_session_file(isolated_home, content)
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 2, (
            f"content {content!r} must resolve as absent, got "
            f"{result.returncode}. stderr: {result.stderr!r}"
        )

    def test_writer_reader_round_trip(self, isolated_home, git_repo):
        """Run capture-session-id.sh for real, then marker.sh activate --
        pins the two-script format as a tested contract instead of two
        independent implementations that happen to agree today."""
        sid = "roundtrip-session"
        run_hook(
            HOOKS_DIR / "capture-session-id.sh",
            {"session_id": sid},
            home=isolated_home,
        )
        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists()

    @pytest.mark.parametrize(
        "invalid_content",
        [
            pytest.param("skipped-mismatch\nMon Jan  1 00:00:00 1970\n", id="mismatch"),
            pytest.param("skipped-old-format\n", id="old_format"),
            pytest.param("", id="empty"),
        ],
    )
    def test_skips_invalid_immediate_ancestor_and_resolves_deeper_one(
        self, isolated_home, git_repo, invalid_content
    ):
        """The walk's fall-through must not just reject a bad entry — it
        must keep walking and resolve a valid entry at a deeper ancestor.
        Every existing deny-path test seeds exactly one entry (at the
        process marker.sh sees as its immediate ancestor) and asserts
        returncode == 2, which can't distinguish 'correctly skipped and
        kept walking, found nothing further up' from a regression that
        aborts the walk on the first bad entry. This test seeds an invalid
        entry at the immediate ancestor AND a valid one at a real deeper
        ancestor (this test process's own parent), so only the
        skip-and-continue behavior can pass it."""
        self._write_raw_session_file(isolated_home, invalid_content)
        sid = "deeper-ancestor-session"
        _seed_session(isolated_home, sid, pid=os.getppid())

        result = _run(["activate", "plan-review"], cwd=git_repo, home=isolated_home)

        assert result.returncode == 0, (
            f"must skip the invalid immediate-ancestor entry and resolve the "
            f"valid deeper one, got {result.returncode}. stderr: {result.stderr!r}"
        )
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists(), (
            "the active marker must be written under the deeper ancestor's "
            "session id, proving the walk actually reached it"
        )

    def test_differing_writer_reader_locale_still_resolves(self, isolated_home, git_repo):
        """Regression guard for the TZ=UTC LC_ALL=C pin itself: run the
        writer under one ambient TZ/LC_ALL and the reader under another,
        assert resolution still succeeds. Without the pin, an ambient
        divergence between the SessionStart hook's shell and the Bash
        tool's shell would make every entry mismatch forever."""
        sid = "locale-mismatch-session"
        run_hook(
            HOOKS_DIR / "capture-session-id.sh",
            {"session_id": sid},
            home=isolated_home,
            extra_env={"TZ": "America/New_York", "LC_ALL": "fr_FR.UTF-8"},
        )
        result = _run(
            ["activate", "plan-review"],
            cwd=git_repo,
            home=isolated_home,
            extra_env={"TZ": "UTC", "LC_ALL": "C"},
        )
        assert result.returncode == 0, result.stderr
        active_file = isolated_home / ".claude" / ".plan-review-active.d" / sid
        assert active_file.exists()
