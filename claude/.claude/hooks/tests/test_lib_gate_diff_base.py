"""Unit tests for _lib.sh's _lib_git_inprogress_state and the anchor and topology behavior of _lib_gate_diff_base."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
from helpers import (
    _run_git,
    bare_remote_with_default_branch,
    build_conflicted_cherry_pick,
    build_conflicted_merge,
    build_conflicted_rebase,
    build_conflicted_revert,
    build_octopus_merge_conflict,
    build_path_without,
    build_rebase_merges_replay_conflict,
    init_git_repo_with_commit,
    push_conflicting_edit_to_origin,
    resolve_conflicted_rebase,
    staged_diff_hash,
)

from ._lib_sh_helpers import (
    _LIB_SH,
    _assert_valid_tree_oid,
    _forge_state_marker,
    _gate_diff_base,
    _staged_diff_hash,
)

# --- _lib_git_inprogress_state / _lib_gate_diff_base --------------------------
#
# Detection precedence, trust anchors, and reference-tree computation for the
# four in-progress git states (merge/rebase/cherry-pick/revert). See
# git-state-safety/SKILL.md's Rule of thumb for the detection recipe and
# docs/hooks.md's "Which tree a marker describes" section for how a gate
# consumes the resulting base.


def _git_inprogress_state(repo: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", f'. {_LIB_SH}; _lib_git_inprogress_state "$1"', "bash", str(repo)],
        capture_output=True,
        text=True,
        check=False,
        env=env if env is not None else dict(os.environ),
    )


class TestGitInprogressStateDetection:
    def test_detects_merge(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_merge(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "merge"

    def test_detects_cherry_pick(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "cherry-pick"

    def test_detects_revert(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "revert"

    def test_detects_rebase_plain(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_rebase(repo)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "rebase"

    def test_rebase_precedence_over_stale_cherry_pick_head(self, tmp_path: Path) -> None:
        """The precedence table requires rebase to win a tie against a
        stale CHERRY_PICK_HEAD alongside rebase-merge/. Older git
        left a real CHERRY_PICK_HEAD during an interactive rebase's pick
        step (each "pick" reuses cherry-pick's own machinery); confirmed
        empirically that the git version running this suite no longer does,
        so the CHERRY_PICK_HEAD half of this fixture is forged directly
        here, alongside a genuine conflicted interactive rebase for the
        rebase half."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        feature_tip = build_conflicted_rebase(repo, interactive=True)
        gitdir = repo / ".git"
        (gitdir / "CHERRY_PICK_HEAD").write_text(feature_tip + "\n")
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == "rebase"

    def test_no_state_in_ordinary_repo(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = _git_inprogress_state(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_undetermined_when_git_missing(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        farm_dir = tmp_path / "path-without-git"
        farm_dir.mkdir()
        restricted_path = build_path_without("git", farm_dir)
        env = {"PATH": restricted_path, "HOME": str(tmp_path)}
        result = _git_inprogress_state(repo, env=env)
        assert result.returncode == 2
        assert result.stdout == ""

    @pytest.mark.parametrize(
        "higher, lower",
        [
            ("rebase", "merge"),
            ("rebase", "revert"),
            ("merge", "cherry-pick"),
            ("merge", "revert"),
            ("cherry-pick", "revert"),
        ],
    )
    def test_precedence_pair_via_forged_files(
        self, tmp_path: Path, higher: str, lower: str
    ) -> None:
        """The remaining five precedence pairs: forge two of the four marker
        shapes directly (no real conflicting operation) and assert the
        higher-precedence state wins, per the table's strict order
        rebase > merge > cherry-pick > revert. Without this, a future
        reordering of the detection if/elif chain regresses silently on any
        pair but rebase-vs-cherry-pick, which the previous test covers with
        a real fixture."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        gitdir = repo / ".git"
        head = _run_git(repo, "rev-parse", "HEAD").strip()
        _forge_state_marker(gitdir, higher, head)
        _forge_state_marker(gitdir, lower, head)
        result = _git_inprogress_state(repo)
        assert result.returncode == 0
        assert result.stdout == higher

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_git_inprogress_state'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert result.stdout == ""


def test_build_conflicted_rebase_pre_resolution_checkpoint_has_all_three_stages(
    tmp_path: Path,
) -> None:
    """build_conflicted_rebase's docstring claims genuine stage 1/2/3
    entries at the pre-resolution checkpoint -- distinct from a 2-way
    add/add conflict, which would carry only stages 2 and 3 with no common
    ancestor. `git ls-files --unmerged` reports one line per populated
    stage for the conflicted path."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)
    unmerged = _run_git(repo, "ls-files", "--unmerged")
    stages = {line.split()[2] for line in unmerged.splitlines() if line}
    assert stages == {"1", "2", "3"}


def test_resolve_conflicted_rebase_advances_to_staged_checkpoint(tmp_path: Path) -> None:
    """The rebase fixture's two checkpoints: pre-add (unresolved, genuine
    stage 1/2/3 entries) and post-resolution (staged, ready for
    `git rebase --continue`)."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)
    resolve_conflicted_rebase(repo)
    status = _run_git(repo, "status", "--porcelain=v1")
    assert "UU" not in status and "AA" not in status
    staged_names = _run_git(repo, "diff", "--cached", "--name-only")
    assert "f" in staged_names
    continue_result = subprocess.run(
        ["git", "rebase", "--continue"],
        cwd=repo,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_EDITOR": "true"},
    )
    assert continue_result.returncode == 0, continue_result.stderr


def test_git_diff_cached_against_unresolved_conflict_git_primitive_fact(
    tmp_path: Path,
) -> None:
    """What `git diff --cached` does against an index still carrying
    unmerged (stage 1/2/3) entries, pinned as a git-primitive fact
    independent of any hook. deny-pii-in-commits.sh and
    deny-private-project-refs.sh both route `git rebase --continue`, and
    their hook-level counterparts are
    test_continue_at_unresolved_conflict_checkpoint_is_a_clean_passthrough in
    test_deny_pii_in_commits.py and test_deny_private_project_refs.py."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    build_conflicted_rebase(repo)  # pre-`git add` checkpoint: unresolved
    result = subprocess.run(
        ["git", "diff", "--cached"], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0
    # git's diff machinery has no single index blob to diff against HEAD for
    # an unmerged path, so `--cached` reports only an "Unmerged path" marker
    # line for it, never the file's actual conflicting content -- a scanner
    # relying on this recipe alone would not see a conflicted file's real
    # content pre-resolution. Substring match, not an exact-stdout pin: the
    # leading marker glyph is git's own free-text diff-output wording, not a
    # machine-stable contract.
    assert "Unmerged path f" in result.stdout


class TestGateDiffBaseNoState:
    def test_no_state_exits_1_with_empty_base(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_no_state_staged_diff_hash_matches_production_recipe(
        self, tmp_path: Path
    ) -> None:
        """Proves no marker on disk invalidates: with no base override,
        _lib_staged_diff_hash must produce the exact byte-identical digest
        today's production `git diff --cached | sha256sum` recipe does."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        result = _staged_diff_hash(repo, "")
        assert result.returncode == 0
        assert result.stdout == staged_diff_hash(repo)

    def test_wrong_arity_returns_could_not_determine(self) -> None:
        result = subprocess.run(
            ["bash", "-c", f'. {_LIB_SH}; _lib_gate_diff_base'],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 2
        assert result.stdout == ""


class TestGateDiffBaseTrustedAnchor:
    """Verification: for each of the four states, with a trusted anchor
    present, _lib_gate_diff_base exits 0 and prints a tree OID."""

    def test_merge_trusted_via_origin_default_branch(self, tmp_path: Path) -> None:
        bare, clone = bare_remote_with_default_branch(tmp_path)
        (clone / "f").write_text("ours-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "ours edits f")
        push_conflicting_edit_to_origin(tmp_path, bare, "f", "origin-edit\n")
        _run_git(clone, "fetch", "-q", "origin")
        result = subprocess.run(
            ["git", "merge", "-q", "origin/main"], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert (clone / ".git" / "MERGE_HEAD").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        assert base_result.stdout != ""
        _assert_valid_tree_oid(clone, base_result.stdout)

    def test_cherry_pick_trusted_via_origin_when_source_already_upstream(
        self, tmp_path: Path
    ) -> None:
        """A cherry-pick passes only when its source is already upstream
        (backporting a landed hotfix)."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        push_conflicting_edit_to_origin(tmp_path, bare, "f", "source-edit\n")
        _run_git(clone, "fetch", "-q", "origin")
        source_oid = _run_git(clone, "rev-parse", "origin/main").strip()
        (clone / "f").write_text("local-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "local edits f")
        result = subprocess.run(
            ["git", "cherry-pick", source_oid], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        assert (clone / ".git" / "CHERRY_PICK_HEAD").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(clone, base_result.stdout)

    def test_revert_trusted_via_head(self, tmp_path: Path) -> None:
        """REVERT_HEAD is an ancestor of HEAD by construction, so a
        genuine revert always trusts via the HEAD anchor."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_revert(repo)
        base_result = _gate_diff_base(repo)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(repo, base_result.stdout)

    def test_rebase_trusted_via_origin_when_replayed_commit_already_pushed(
        self, tmp_path: Path
    ) -> None:
        """REBASE_HEAD reaches origin/<default> when the replayed commit was
        already pushed there before the rebase started, so the origin anchor
        trusts it. This is atypical: a rebase ordinarily fails both anchors.
        REBASE_HEAD is the pre-rebase commit being replayed. Mid-rebase HEAD
        is the new base plus already-replayed commits."""
        bare, clone = bare_remote_with_default_branch(tmp_path)
        _run_git(clone, "checkout", "-qb", "upstream")
        (clone / "f").write_text("upstream-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "upstream edits f")
        _run_git(clone, "checkout", "-q", "main")
        (clone / "f").write_text("feature-edit\n")
        _run_git(clone, "add", "f")
        _run_git(clone, "commit", "-qm", "feature edits f")
        _run_git(clone, "push", "-q", "origin", "main")
        result = subprocess.run(
            ["git", "rebase", "upstream"], cwd=clone, capture_output=True, text=True
        )
        assert result.returncode != 0, result.stdout + result.stderr
        gitdir = clone / ".git"
        assert (gitdir / "rebase-apply").exists() or (gitdir / "rebase-merge").exists()

        base_result = _gate_diff_base(clone)
        assert base_result.returncode == 0
        _assert_valid_tree_oid(clone, base_result.stdout)


class TestGateDiffBaseSha256ObjectFormat:
    def test_sha256_repo_mid_revert_returns_valid_64_hex_tree_oid(
        self, tmp_path: Path
    ) -> None:
        """state_oid's shape validation accepts both a 40-hex SHA-1 and a
        64-hex SHA-2 oid (_lib.sh's `_lib_gate_diff_base`), but every other
        fixture in this file builds an ordinary SHA-1 repo -- this is the
        only one that exercises the 64-hex branch end-to-end."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(
            ["git", "init", "-q", "--object-format=sha256", "-b", "main", str(repo)],
            check=True,
        )
        subprocess.run(["git", "config", "user.email", "t@example.invalid"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
        build_conflicted_revert(repo)

        result = _gate_diff_base(repo)

        assert result.returncode == 0
        assert re.fullmatch(r"[0-9a-f]{64}", result.stdout), (
            f"expected a 64-hex SHA-256 tree oid, got {result.stdout!r}"
        )
        _assert_valid_tree_oid(repo, result.stdout)


class TestGateDiffBaseAnchorNamespaceShadow:
    """A local branch or tag literally named `origin/<default>` outranks the
    remote-tracking ref under git's short-name resolution, so the anchor
    check must use the fully-qualified refs/remotes/ path."""

    @pytest.mark.parametrize("shadow_kind", ["branch", "tag"])
    @pytest.mark.parametrize("has_remote_tracking_ref", [True, False])
    @pytest.mark.parametrize("state", ["merge", "cherry-pick"])
    def test_local_ref_named_like_remote_tracking_ref_is_not_the_anchor(
        self, tmp_path: Path, shadow_kind: str, has_remote_tracking_ref: bool, state: str
    ) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("unreviewed\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "unreviewed commit")
        unreviewed_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        if has_remote_tracking_ref:
            _run_git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
        if shadow_kind == "branch":
            _run_git(repo, "branch", "origin/main", unreviewed_oid)
        else:
            _run_git(repo, "tag", "origin/main", unreviewed_oid)
        _forge_state_marker(repo / ".git", state, unreviewed_oid)

        # Precondition: the short name resolves to the shadow, so the test
        # fails against an anchor spelled `origin/<default>`.
        shadowed_oid = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "origin/main"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert shadowed_oid == unreviewed_oid

        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestGateDiffBaseUntrustedAnchor:
    def test_reaches_neither_anchor_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        """A cherry-pick whose source was never pushed anywhere over-gates
        -- the right answer, since that content genuinely has not been
        reviewed on this branch."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_conflicted_cherry_pick(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    # No "merge": git refuses to merge unrelated histories whatever the anchor decides.
    @pytest.mark.parametrize("state", ["rebase", "cherry-pick", "revert"])
    def test_forged_ref_pointing_at_unreachable_commit_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A state ref pointed at a commit reachable from neither anchor
        -- an orphan-history commit, sharing no ancestry with the
        checked-out branch at all -- must not be trusted."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-q", "--orphan", "orphan")
        (repo / "orphan.txt").write_text("z\n")
        _run_git(repo, "add", "orphan.txt")
        _run_git(repo, "commit", "-qm", "orphan root commit")
        # A parent, so the <oid>^ in the merge-tree commands resolves.
        (repo / "orphan.txt").write_text("z2\n")
        _run_git(repo, "add", "orphan.txt")
        _run_git(repo, "commit", "-qm", "orphan commit")
        orphan_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "rev-parse", "--verify", f"{orphan_oid}^")
        _run_git(repo, "checkout", "-q", "main")
        _forge_state_marker(repo / ".git", state, orphan_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "merge", "cherry-pick", "revert"])
    def test_reachable_only_from_fabricated_remote_tracking_ref_still_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A declined third anchor, pinned as a negative assertion: a
        commit reachable only from a hand-created
        refs/remotes/origin/<fabricated-name> must not be trusted, since
        _lib_default_branch_or_guess's candidate probe is deliberately
        narrow (exactly main/master/develop, never a refs/remotes/* pattern)
        and never resolves to this name."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("side\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        _run_git(repo, "update-ref", "refs/remotes/origin/totally-not-the-default", side_oid)
        _forge_state_marker(repo / ".git", state, side_oid)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    @pytest.mark.parametrize("state", ["rebase", "merge", "cherry-pick", "revert"])
    def test_resolvable_non_hex_state_ref_falls_back(
        self, tmp_path: Path, state: str
    ) -> None:
        """A resolvable but non-hex state ref must fall back to the empty base.
        A state ref file is plain, unauthenticated content that anyone with
        filesystem access could write, so it is not proof of a real git
        operation. `HEAD` is its own ancestor, so without state_oid's shape
        guard it would pass the anchor check and reach merge-tree, producing a
        real tree OID. A `--flag`-shaped payload would not discriminate,
        because git's CLI parser rejects an unknown option in
        merge-base/merge-tree independent of the guard."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        # A parent, so the HEAD^ in the rebase/cherry-pick/revert merge-tree commands resolves.
        (repo / "f.txt").write_text("second\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "second commit")
        _run_git(repo, "rev-parse", "--verify", "HEAD^")
        _forge_state_marker(repo / ".git", state, "HEAD")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestGateDiffBaseTopologyFallback:
    def test_octopus_merge_falls_back_to_empty_base(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_octopus_merge_conflict(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_octopus_merge_head_falls_back_even_when_first_line_alone_is_trusted(
        self, tmp_path: Path
    ) -> None:
        """A multi-line MERGE_HEAD must fall back to the empty base, even
        though line 1 alone is trusted. Line 1 is by itself a genuine
        ancestor of HEAD, with only line 2 unreachable. This is the stronger
        adversarial sub-case, distinct from the both-untrusted-lines fixture
        above. state_oid's shape validation rejects the multi-line value
        before any anchor check runs. Without that guard,
        `git merge-base --is-ancestor` still fails to parse a multi-line
        revision, regardless of which line would individually pass."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        trusted_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-qb", "untrusted-line")
        (repo / "f.txt").write_text("untrusted\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "untrusted commit")
        untrusted_oid = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        (repo / ".git" / "MERGE_HEAD").write_text(f"{trusted_oid}\n{untrusted_oid}\n")
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""

    def test_rebase_merges_replay_of_merge_commit_falls_back_to_empty_base(
        self, tmp_path: Path
    ) -> None:
        """The --rebase-merges hazard: REBASE_HEAD names a merge commit, so
        REBASE_HEAD^ would silently resolve to parent 1 rather than
        erroring. Closed because the replayed merge commit reaches neither
        anchor, not by a second mechanism."""
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        build_rebase_merges_replay_conflict(repo)
        result = _gate_diff_base(repo)
        assert result.returncode == 1
        assert result.stdout == ""


class TestMergeBaseIsAncestorPrimitiveContract:
    """Pins git's own documented `merge-base --is-ancestor` exit contract
    as a suite fact, since _lib_gate_diff_base's trust check depends on
    treating every non-zero exit identically as "not trusted"."""

    def test_ancestor_returns_zero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        first = _run_git(repo, "rev-parse", "HEAD").strip()
        (repo / "f.txt").write_text("y\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "second")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", first, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode == 0

    def test_non_ancestor_returns_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        _run_git(repo, "checkout", "-qb", "side")
        (repo / "f.txt").write_text("side\n")
        _run_git(repo, "add", "f.txt")
        _run_git(repo, "commit", "-qm", "side commit")
        side = _run_git(repo, "rev-parse", "HEAD").strip()
        _run_git(repo, "checkout", "-q", "main")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", side, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode != 0

    def test_unresolvable_oid_returns_nonzero(self, tmp_path: Path) -> None:
        repo = tmp_path / "repo"
        init_git_repo_with_commit(repo, branch="main")
        result = subprocess.run(
            ["git", "merge-base", "--is-ancestor", "0" * 40, "HEAD"], cwd=repo, capture_output=True
        )
        assert result.returncode != 0


def test_git_diff_cached_accepts_bare_tree_oid(tmp_path: Path) -> None:
    """_lib_gate_diff_base's stdout is a bare tree OID, and callers diff
    staged content against it directly -- pins that `git diff --cached`
    accepts one."""
    repo = tmp_path / "repo"
    init_git_repo_with_commit(repo, branch="main")
    tree_oid = _run_git(repo, "rev-parse", "HEAD^{tree}").strip()
    (repo / "f.txt").write_text("y\n")
    _run_git(repo, "add", "f.txt")
    result = subprocess.run(
        ["git", "diff", "--cached", tree_oid], cwd=repo, capture_output=True, text=True
    )
    assert result.returncode == 0


def test_merge_tree_merge_base_flag_changes_computed_tree(tmp_path: Path) -> None:
    """--merge-base='s effect on the computed tree is observed here, not
    merely its acceptance as a flag. root -> middle (changes "a") -> tip
    (changes "b" only, so
    tip still carries middle's "a" change). Auto-detecting the merge-base
    of (root, tip) yields root (an ancestor), so merging root and tip
    trivially reproduces tip's own tree. Forcing --merge-base=middle
    instead treats "a" as also having changed on root's side (a revert of
    middle's change), producing a materially different, still
    non-conflicting tree."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-q", "-b", "main")
    _run_git(repo, "config", "user.email", "t@example.invalid")
    _run_git(repo, "config", "user.name", "t")
    (repo / "a").write_text("1\n")
    (repo / "b").write_text("1\n")
    _run_git(repo, "add", "a", "b")
    _run_git(repo, "commit", "-qm", "root")
    root = _run_git(repo, "rev-parse", "HEAD").strip()
    (repo / "a").write_text("2\n")
    _run_git(repo, "add", "a")
    _run_git(repo, "commit", "-qm", "middle changes a")
    middle = _run_git(repo, "rev-parse", "HEAD").strip()
    (repo / "b").write_text("2\n")
    _run_git(repo, "add", "b")
    _run_git(repo, "commit", "-qm", "tip changes b")
    tip = _run_git(repo, "rev-parse", "HEAD").strip()

    auto = _run_git(repo, "merge-tree", "--write-tree", root, tip).strip().splitlines()[0]
    forced = _run_git(
        repo, "merge-tree", "--write-tree", f"--merge-base={middle}", root, tip
    ).strip().splitlines()[0]
    assert auto != forced
    assert auto == _run_git(repo, "rev-parse", f"{tip}^{{tree}}").strip()
