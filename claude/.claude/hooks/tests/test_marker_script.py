"""Tests for claude/.claude/scripts/marker.sh."""
from __future__ import annotations

import os
import re
import subprocess

import pytest
from helpers import HOOKS_DIR, SCRIPTS_DIR, bash_input, run_hook

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
        marker_dir = isolated_home / ".claude" / "code-review-markers"
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
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        files = list(marker_dir.iterdir())
        assert len(files) == 1
        assert files[0].name.endswith(f".{sid}")

    def test_write_plan_review_stores_hash_not_literal(self, isolated_home, git_repo):
        """write plan-review must store _lib_active_plan_hash's output (a
        sha256 hex digest of the active plan set), not the legacy literal
        'reviewed' existence-only sentinel."""
        self._seed_session(isolated_home)
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
        sid = self._seed_session(isolated_home)
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
        marker_dir = isolated_home / ".claude" / "code-review-markers"
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
        marker_dir = isolated_home / ".claude" / "code-review-markers"
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

    def _seed_session(self, home):
        sessions_dir = home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = "test-session-naming"
        (sessions_dir / str(os.getpid())).write_text(sid)
        return sid

    @pytest.mark.parametrize("skill", WRITE_SKILLS)
    def test_write_lands_in_skill_derived_directory(
        self, skill, isolated_home, git_repo
    ):
        sid = self._seed_session(isolated_home)
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
        self._seed_session(isolated_home)
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
        sessions_dir = isolated_home / ".claude" / "sessions"
        sessions_dir.mkdir(parents=True, exist_ok=True)
        sid = "test-session-roundtrip"
        (sessions_dir / str(os.getpid())).write_text(sid)

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
