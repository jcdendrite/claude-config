"""Markers must describe the tree the reviewed work lives in.

A marker's path (repo hash) and its value (staged-diff hash, HEAD SHA, plan
set) are both derived from a resolved repo root. When the writer and the
readers resolve that root from ambient shell state, a session whose shell sits
in a different working tree of the same repo writes and then satisfies a gate
describing a tree nobody reviewed — the write and the read drift together, so
the pair stays self-consistent and the gate passes.

Two invariants pin that shut:
  * marker.sh refuses to write from the main working tree while worktree
    enforcement is active (there is no payload it could import a trusted
    directory from, so refusing beats guessing).
  * the reading hooks resolve their root from the hook payload's `.cwd` and
    thread that one root through every git call.
"""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest
from helpers import (
    HOOKS_DIR,
    SCRIPTS_DIR,
    run_hook,
    staged_diff_hash,
    write_marker,
    write_skill_review_marker,
)

from .conftest import _seed_session as _seed_session_at

MARKER_SCRIPT = SCRIPTS_DIR / "marker.sh"
REQUIRE_CODE_REVIEW = HOOKS_DIR / "require-code-review.sh"
REQUIRE_PLAN_REVIEW = HOOKS_DIR / "require-plan-review.sh"
REQUIRE_SKILL_REVIEW = HOOKS_DIR.parent.parent.parent / "plugins" / "skill-management" / "hooks" / "require-skill-review.sh"


def _init_repo(path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _run_marker(args: list[str], cwd, home) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(MARKER_SCRIPT)] + args,
        cwd=cwd,
        env={**os.environ, "HOME": str(home)},
        capture_output=True,
        text=True,
    )


def _seed_session(home) -> str:
    """marker.sh resolves its session id by walking process ancestors, so the
    session file must be keyed to this pytest process's pid."""
    sid = "test-session-keying"
    _seed_session_at(home, sid)
    return sid


def _repo_hash(path) -> str:
    return hashlib.sha256(str(path).encode()).hexdigest()


def _stage_a_change(tree) -> None:
    (tree / "work.txt").write_text("staged content\n")
    subprocess.run(["git", "add", "work.txt"], cwd=tree, check=True)


def _stage_a_skill_md_change(tree) -> None:
    """Stage a real SKILL.md-matching path, so `write skill-review` sees a
    non-empty pathspec-scoped diff and actually writes a marker rather than
    hitting the empty-diff no-marker branch."""
    skill_dir = tree / "claude-skills" / "skills" / "worktree-keying-test-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# test skill\n")
    subprocess.run(["git", "add", str(skill_md)], cwd=tree, check=True)


class TestMarkerScriptRefusesMainTreeUnderEnforcement:
    """The five states the fail-closed check has to distinguish. Only the
    first is a refusal — the rest must behave exactly as they did before the
    check existed, which is what keeps a solo main-tree repo working."""

    def test_denies_from_main_tree_when_worktree_exists(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, _wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(repo)

        result = _run_marker(["write", "code-review"], cwd=repo, home=isolated_home)

        assert result.returncode == 2, (
            f"expected refusal from the main tree under enforcement, got "
            f"{result.returncode}. stderr: {result.stderr!r}"
        )
        marker_dir = isolated_home / ".claude" / "code-review-markers"
        stray = list(marker_dir.iterdir()) if marker_dir.exists() else []
        assert stray == [], f"refusal must not write a marker, found {stray}"

    def test_denial_names_the_recovery_action(self, isolated_home, opted_in_with_worktree):
        """This check fires after the review has already been done, and a
        dead-end denial is what pushes a session toward disabling enforcement
        outright. The message has to say how to get unstuck."""
        repo, _wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(repo)

        result = _run_marker(["write", "code-review"], cwd=repo, home=isolated_home)

        assert "EnterWorktree" in result.stderr, (
            f"denial must name the re-anchoring action, got: {result.stderr!r}"
        )

    def test_allows_from_the_linked_worktree(self, isolated_home, opted_in_with_worktree):
        _repo, wt = opted_in_with_worktree
        sid = _seed_session(isolated_home)
        _stage_a_change(wt)

        result = _run_marker(["write", "code-review"], cwd=wt, home=isolated_home)

        assert result.returncode == 0, result.stderr
        marker = isolated_home / ".claude" / "code-review-markers" / f"{_repo_hash(wt)}.{sid}"
        assert marker.exists(), (
            "marker must be keyed to the worktree path's hash; "
            f"found {list((isolated_home / '.claude' / 'code-review-markers').iterdir())}"
        )

    def test_allows_opted_in_repo_with_no_worktree(self, isolated_home, opted_in_repo):
        """Opted into enforcement, but no worktree exists yet.

        The refusal requires a linked worktree to exist, because the failure it
        prevents needs a second tree: a marker written from the main tree is
        only wrong if the reviewed work lives somewhere else. With no worktree
        there is nowhere else, so the marker correctly describes the only tree
        there is.

        Refusing here would wedge a repo whose staged state was produced
        outside Claude Code's gated tool calls. The worktree hooks gate tool
        calls (PreToolUse on Edit/Write and on Bash-invoked git), not ambient
        git state — a hand-staged edit in a terminal or editor, a CI checkout,
        or work staged before the repo opted in all reach main-tree staged
        content untouched by any hook.
        """
        sid = _seed_session(isolated_home)
        _stage_a_change(opted_in_repo)

        result = _run_marker(["write", "code-review"], cwd=opted_in_repo, home=isolated_home)

        assert result.returncode == 0, (
            f"an opted-in repo with no worktree must still be able to record a "
            f"review. stderr: {result.stderr!r}"
        )
        marker = (
            isolated_home / ".claude" / "code-review-markers"
            / f"{_repo_hash(opted_in_repo)}.{sid}"
        )
        assert marker.exists()

    def test_denies_once_a_worktree_appears(self, isolated_home, opted_in_repo, tmp_path):
        """The transition that flips the verdict: same repo, same main-tree
        cwd, but a linked worktree now exists — so a main-tree marker could
        describe the wrong tree, and the write is refused."""
        _seed_session(isolated_home)
        _stage_a_change(opted_in_repo)
        assert _run_marker(
            ["write", "code-review"], cwd=opted_in_repo, home=isolated_home
        ).returncode == 0

        subprocess.run(
            ["git", "worktree", "add", "-b", "feature", str(tmp_path / "wt")],
            cwd=opted_in_repo,
            check=True,
            capture_output=True,
        )
        result = _run_marker(["write", "code-review"], cwd=opted_in_repo, home=isolated_home)

        assert result.returncode == 2, (
            f"a main-tree write must be refused once a worktree exists. "
            f"stderr: {result.stderr!r}"
        )

    def test_allows_when_enforcement_inactive(self, isolated_home, non_opted_repo):
        sid = _seed_session(isolated_home)
        _stage_a_change(non_opted_repo)

        result = _run_marker(["write", "code-review"], cwd=non_opted_repo, home=isolated_home)

        assert result.returncode == 0, result.stderr
        marker = (
            isolated_home / ".claude" / "code-review-markers"
            / f"{_repo_hash(non_opted_repo)}.{sid}"
        )
        assert marker.exists()

    def test_denies_from_a_subdirectory_of_the_main_tree(
        self, isolated_home, opted_in_with_worktree
    ):
        """The check must key off git's view of the tree, not off whether cwd
        happens to equal the repo root."""
        repo, _wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(repo)
        subdir = repo / "nested" / "deeper"
        subdir.mkdir(parents=True)

        result = _run_marker(["write", "code-review"], cwd=subdir, home=isolated_home)

        assert result.returncode == 2, (
            f"a subdirectory of the main tree is still the main tree. "
            f"stderr: {result.stderr!r}"
        )

    def test_allows_from_a_subdirectory_of_the_worktree(
        self, isolated_home, opted_in_with_worktree
    ):
        _repo, wt = opted_in_with_worktree
        sid = _seed_session(isolated_home)
        _stage_a_change(wt)
        subdir = wt / "nested" / "deeper"
        subdir.mkdir(parents=True)

        result = _run_marker(["write", "code-review"], cwd=subdir, home=isolated_home)

        assert result.returncode == 0, result.stderr
        # Keyed to the worktree ROOT, not to the subdirectory it was run from.
        marker = isolated_home / ".claude" / "code-review-markers" / f"{_repo_hash(wt)}.{sid}"
        assert marker.exists()

    def test_clear_stale_is_unaffected(self, isolated_home, opted_in_with_worktree):
        """clear-stale evicts orphaned active markers by liveness, and has no
        repo identity at all. Routing it through the repo-root resolver would
        make main-tree recovery impossible exactly when it is needed."""
        repo, _wt = opted_in_with_worktree
        active_dir = isolated_home / ".claude" / ".plan-review-active.d"
        active_dir.mkdir(parents=True)
        (active_dir / "dead-session").write_text("999999999\n")

        result = _run_marker(["clear-stale"], cwd=repo, home=isolated_home)

        assert result.returncode == 0, (
            f"clear-stale must work from the main tree under enforcement. "
            f"stderr: {result.stderr!r}"
        )
        assert not (active_dir / "dead-session").exists(), "orphan should have been evicted"


class TestMarkerScriptRefusalTradeoffRemovingTheWorktreeReopensMainTreeWrites:
    """Pins a deliberate, documented tradeoff -- see the "no linked worktree
    exists" branch of `_refuse_main_tree_under_enforcement`'s comment in
    marker.sh, and docs/hooks.md's "Which tree a marker describes" section.

    The refusal is keyed to whether a linked worktree currently exists on
    disk, not to whether one ever existed. An agent told to re-enter the
    worktree (`EnterWorktree{path: ...}`) could instead `rm -rf` it without
    pruning and satisfy the exact same "no second tree to confuse the first
    with" condition that lets a solo main-tree repo write freely -- reopening
    main-tree marker writes rather than re-anchoring in the worktree. This
    test does not assert that behavior should change; it pins the transition
    as a visible, re-reviewable decision rather than an unnoticed side door.
    """

    def test_removing_the_unpruned_worktree_reopens_main_tree_writes(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, wt = opted_in_with_worktree
        sid = _seed_session(isolated_home)
        _stage_a_change(repo)

        refused = _run_marker(["write", "code-review"], cwd=repo, home=isolated_home)
        assert refused.returncode == 2, (
            f"expected the live-worktree refusal as this test's starting "
            f"condition. stderr: {refused.stderr!r}"
        )

        subprocess.run(["rm", "-rf", str(wt)], check=True)

        allowed = _run_marker(["write", "code-review"], cwd=repo, home=isolated_home)
        assert allowed.returncode == 0, (
            f"removing the (unpruned) worktree directory is documented to "
            f"reopen main-tree writes -- production behavior is unchanged by "
            f"this test. stderr: {allowed.stderr!r}"
        )
        marker = isolated_home / ".claude" / "code-review-markers" / f"{_repo_hash(repo)}.{sid}"
        assert marker.exists()


@pytest.mark.parametrize("skill", ["code-review", "skill-review", "plan-review", "ready-for-review"])
class TestMarkerScriptRefusalCoversEveryWriteArm:
    """The refusal (`_refuse_main_tree_under_enforcement`, called from
    `_resolve_repo_root`) runs as the second step of every `write` arm, before
    any arm-specific precondition — the staged SKILL.md diff, the active plan
    set, HEAD — is even read. A regression that bypassed `_resolve_repo_root`
    in one arm only would leave the other three refusing correctly, so a
    deny-path test that covers a single arm cannot catch it. This class
    exercises all four.

    Each arm's minimal setup is chosen so that, absent the refusal, the write
    would otherwise succeed: `_stage_a_change` gives code-review a staged
    diff and ready-for-review a resolvable HEAD; skill-review additionally
    stages a real SKILL.md-matching path via `_stage_a_skill_md_change`, so
    "would otherwise succeed" keeps meaning "writes a marker" rather than
    merely exiting 0 via the empty-diff no-marker branch; plan-review hashes
    cleanly with no `.claude/plans/` at all (empty hash, still a successful
    write). That is what makes returncode 2 attributable to the refusal
    specifically, not to some other precondition failing.
    """

    def test_denies_from_main_tree_when_worktree_exists(
        self, isolated_home, opted_in_with_worktree, skill
    ):
        repo, _wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(repo)
        if skill == "skill-review":
            _stage_a_skill_md_change(repo)

        result = _run_marker(["write", skill], cwd=repo, home=isolated_home)

        assert result.returncode == 2, (
            f"expected refusal for 'write {skill}' from the main tree under "
            f"enforcement, got {result.returncode}. stderr: {result.stderr!r}"
        )

    def test_allows_from_the_linked_worktree(
        self, isolated_home, opted_in_with_worktree, skill
    ):
        _repo, wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(wt)
        if skill == "skill-review":
            _stage_a_skill_md_change(wt)

        result = _run_marker(["write", skill], cwd=wt, home=isolated_home)

        assert result.returncode == 0, (
            f"'write {skill}' from the linked worktree must not be refused. "
            f"stderr: {result.stderr!r}"
        )


class TestMarkerScriptSingleInvocationConsistency:
    """One marker.sh subprocess must derive every value from one resolved root.

    Scope, stated precisely because it is easy to overclaim: these tests do NOT
    discriminate a missed `-C "$REPO_ROOT"` at one call site. marker.sh derives
    its root from ambient cwd (it has no payload to read one from) and performs
    no internal `cd`, so within a single invocation a bare git call and a
    `-C "$REPO_ROOT"` one resolve to the same directory by construction. What
    these pin is the outcome — every derived value describes the worktree and
    none leaks main-tree state — which is the property that must hold however
    the root is threaded. The `-C` threading itself is defense against a future
    edit introducing a `cd`; that regression would surface here only if such a
    `cd` were added.
    """

    def test_every_derived_value_reflects_the_worktree(
        self, isolated_home, opted_in_with_worktree
    ):
        repo, wt = opted_in_with_worktree
        sid = _seed_session(isolated_home)

        # Divergent staged state in each tree: if any git call leaks back to
        # the main tree, the stored hash is the main tree's, not the worktree's.
        (repo / "main-only.txt").write_text("main tree content\n")
        subprocess.run(["git", "add", "main-only.txt"], cwd=repo, check=True)
        (wt / "worktree-only.txt").write_text("worktree content\n")
        subprocess.run(["git", "add", "worktree-only.txt"], cwd=wt, check=True)

        result = _run_marker(["write", "code-review"], cwd=wt, home=isolated_home)
        assert result.returncode == 0, result.stderr

        marker = isolated_home / ".claude" / "code-review-markers" / f"{_repo_hash(wt)}.{sid}"
        assert marker.exists(), "marker path must be keyed to the worktree"

        expected = subprocess.run(
            ["git", "diff", "--cached"], cwd=wt, capture_output=True, text=True, check=True
        ).stdout
        main_tree_diff = subprocess.run(
            ["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout
        assert expected != main_tree_diff, "fixture bug: the two trees must differ"

        stored = marker.read_text().strip()
        assert stored == hashlib.sha256(expected.encode()).hexdigest(), (
            "stored hash must be the worktree's staged diff"
        )
        assert stored != hashlib.sha256(main_tree_diff.encode()).hexdigest(), (
            "stored hash leaked the main tree's staged diff"
        )

    def test_ready_for_review_records_the_worktree_head(
        self, isolated_home, opted_in_with_worktree
    ):
        """`write ready-for-review` stores HEAD. The worktree is on its own
        branch, so a bare `git rev-parse HEAD` would record the wrong commit
        once the two branches diverge."""
        repo, wt = opted_in_with_worktree
        sid = _seed_session(isolated_home)

        (wt / "advance.txt").write_text("advance the worktree branch\n")
        subprocess.run(["git", "add", "advance.txt"], cwd=wt, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "advance"], cwd=wt, check=True)

        result = _run_marker(["write", "ready-for-review"], cwd=wt, home=isolated_home)
        assert result.returncode == 0, result.stderr

        marker = (
            isolated_home / ".claude" / "ready-for-review-markers" / f"{_repo_hash(wt)}.{sid}"
        )
        wt_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=wt, capture_output=True, text=True, check=True
        ).stdout.strip()
        main_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        assert wt_head != main_head, "fixture bug: branches must have diverged"
        assert marker.read_text().strip() == wt_head


class TestReaderHooksPreferPayloadCwd:
    """Each converted hook must key off the payload's `.cwd`, not the ambient
    cwd of the hook process.

    Scope note: this pins the *hook's* contract given a payload. It does not
    establish what the harness puts in `.cwd` — that was settled against the
    live harness, and no hand-constructed payload could distinguish the
    hypotheses.
    """

    def test_code_review_gate_reads_payload_cwd(self, isolated_home, git_repo, tmp_path):
        """Run the hook from an unrelated directory while the payload points at
        the staged repo. Ambient resolution would find no repo and exit 0
        (allow); payload resolution finds the staged diff and denies."""
        elsewhere = tmp_path / "unrelated"
        elsewhere.mkdir()

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(git_repo),
        }
        decision = run_hook(
            REQUIRE_CODE_REVIEW, payload, cwd=elsewhere, home=isolated_home
        )
        assert decision == "deny", (
            "hook resolved its repo from ambient cwd instead of the payload"
        )

    def test_code_review_gate_allows_when_payload_cwd_has_no_staged_diff(
        self, isolated_home, opted_in_repo, git_repo
    ):
        """The mirror of the case above: ambient cwd sits in a repo WITH a
        staged diff, payload cwd sits in one without. Keying off ambient state
        would deny; keying off the payload allows."""
        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(opted_in_repo),
        }
        decision = run_hook(
            REQUIRE_CODE_REVIEW, payload, cwd=git_repo, home=isolated_home
        )
        assert decision == "allow", (
            "hook denied using the ambient repo's staged diff rather than the payload's"
        )

    def test_plan_review_gate_reads_payload_cwd(self, isolated_home, git_repo, tmp_path):
        """An un-reviewed plan file in the payload's repo must arm the gate even
        though the hook process runs from an unrelated directory."""
        elsewhere = tmp_path / "unrelated-plan"
        elsewhere.mkdir()
        plans = git_repo / ".claude" / "plans"
        plans.mkdir(parents=True)
        (plans / "p.md").write_text("# plan\n")

        payload = {
            "tool_name": "ExitPlanMode",
            "tool_input": {"plan": "do the thing"},
            "cwd": str(git_repo),
        }
        decision = run_hook(
            REQUIRE_PLAN_REVIEW, payload, cwd=elsewhere, home=isolated_home
        )
        assert decision == "deny", (
            "plan-review gate resolved its repo from ambient cwd instead of the payload"
        )

    def test_falls_back_to_process_cwd_when_payload_has_no_cwd(
        self, isolated_home, git_repo
    ):
        """Every existing test payload omits `.cwd`; the fallback is what keeps
        those green, so it is an invariant in its own right."""
        payload = {"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}}
        decision = run_hook(REQUIRE_CODE_REVIEW, payload, cwd=git_repo, home=isolated_home)
        assert decision == "deny"


class TestReaderHooksHashComparisonUsesPayloadCwd:
    """The empty-diff early exit and the authorizing hash comparison are two
    separate `-C "$REPO_ROOT"` sites in each of these hooks. A payload whose
    `.cwd` differs from the ambient cwd, where BOTH trees carry a non-empty
    staged diff, cannot short-circuit at the early exit — so these tests pin
    down that the hash comparison itself reads the payload's tree, which the
    early-exit tests elsewhere in this file cannot distinguish (they never
    reach the hash line with a live marker in play).
    """

    def test_code_review_gate_hashes_payload_cwd_not_ambient_cwd(
        self, isolated_home, git_repo, tmp_path
    ):
        other_repo = tmp_path / "other-repo"
        _init_repo(other_repo)
        (other_repo / "f.txt").write_text("first\n")
        subprocess.run(["git", "add", "f.txt"], cwd=other_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=other_repo, check=True)
        (other_repo / "f.txt").write_text("first\nother-repo-only\n")
        subprocess.run(["git", "add", "f.txt"], cwd=other_repo, check=True)

        # git_repo (the ambient cwd) already carries its own, different,
        # staged diff (from the fixture) -- so both trees are non-empty and
        # the early exit at the empty-diff check cannot short-circuit either.
        write_marker(isolated_home, other_repo, staged_diff_hash(other_repo))

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(other_repo),
        }
        decision = run_hook(REQUIRE_CODE_REVIEW, payload, cwd=git_repo, home=isolated_home)
        assert decision == "allow", (
            "the hash comparison must hash the payload cwd's staged diff, not "
            "the ambient cwd's -- a regression dropping -C from CURRENT_HASH "
            "would hash the wrong tree's diff and deny here"
        )

    def test_skill_review_gate_hashes_payload_cwd_not_ambient_cwd(
        self, isolated_home, git_repo, tmp_path
    ):
        other_repo = tmp_path / "other-skill-repo"
        _init_repo(other_repo)
        other_skill = other_repo / "claude-skills" / "skills" / "s" / "SKILL.md"
        other_skill.parent.mkdir(parents=True)
        other_skill.write_text("## initial\n")
        subprocess.run(["git", "add", "."], cwd=other_repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=other_repo, check=True)
        other_skill.write_text("## initial\n## other repo change\n")
        subprocess.run(["git", "add", "."], cwd=other_repo, check=True)

        # Ambient cwd (git_repo) also has a staged SKILL.md diff, with
        # different content -- both trees non-empty, so neither early exit
        # (no-SKILL.md-staged, empty-diff) can short-circuit.
        ambient_skill = git_repo / "claude-skills" / "skills" / "s" / "SKILL.md"
        ambient_skill.parent.mkdir(parents=True, exist_ok=True)
        ambient_skill.write_text("## ambient repo change\n")
        subprocess.run(
            ["git", "add", str(ambient_skill.relative_to(git_repo))], cwd=git_repo, check=True
        )

        write_skill_review_marker(isolated_home, other_repo)

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(other_repo),
        }
        decision = run_hook(REQUIRE_SKILL_REVIEW, payload, cwd=git_repo, home=isolated_home)
        assert decision == "allow", (
            "the SKILL.md hash comparison must hash the payload cwd's staged "
            "diff, not the ambient cwd's"
        )


class TestReaderWriterAgreement:
    """In the non-drifted case the writer's resolved root and the reader's
    payload-derived root must produce the same repo hash. Without this, a
    future change to `.cwd` semantics would silently restore the wrong-tree
    pass instead of failing a test."""

    def test_marker_written_in_worktree_satisfies_the_gate_for_that_worktree(
        self, isolated_home, opted_in_with_worktree
    ):
        _repo, wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(wt)

        assert _run_marker(["write", "code-review"], cwd=wt, home=isolated_home).returncode == 0

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(wt),
        }
        decision = run_hook(REQUIRE_CODE_REVIEW, payload, cwd=wt, home=isolated_home)
        assert decision == "allow", (
            "a review recorded from the worktree must satisfy the gate for that worktree"
        )

    def test_marker_written_in_worktree_does_not_satisfy_the_main_tree(
        self, isolated_home, opted_in_with_worktree
    ):
        """The point of keying on the worktree path rather than on
        --git-common-dir: a review of one tree must not authorize a commit
        described by another."""
        repo, wt = opted_in_with_worktree
        _seed_session(isolated_home)
        _stage_a_change(wt)
        # Different staged content in the main tree.
        (repo / "other.txt").write_text("different\n")
        subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True)

        assert _run_marker(["write", "code-review"], cwd=wt, home=isolated_home).returncode == 0

        payload = {
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m x"},
            "cwd": str(repo),
        }
        decision = run_hook(REQUIRE_CODE_REVIEW, payload, cwd=repo, home=isolated_home)
        assert decision == "deny", (
            "a worktree review must not authorize a main-tree commit"
        )
