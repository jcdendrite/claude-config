"""Tests for evals/review_bench's defect schema, miners, and confirmation
CLI (dispatch 1a). Offline throughout: mine_szz and mine_review_rounds
fixtures are real tmp-path git repos (a local bare repo standing in for
`origin` where a PR-head fetch is exercised) and synthetic transcript
JSONL. No test launches `claude`.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
import run_review_bench
from review_bench import defects, mine_review_rounds, mine_szz
from review_bench.defects import Candidate, ConfirmedDefect
from transcript_analysis import corpus, review_rounds, reviewer_yield

_SHA_A = "a" * 40
_SHA_B = "b" * 40
_SHA_C = "c" * 40


# --- git-repo fixture helpers ------------------------------------------------


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True)
    return result.stdout


def _init_repo(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test")
    return path


def _write(repo: Path, rel_path: str, content: str) -> None:
    target = repo / rel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _set_origin_main(repo: Path, sha: str) -> None:
    """Stands in for a real `origin` remote so mine_szz's `origin/main`
    default resolves without a network fetch."""
    subprocess.run(["git", "update-ref", "refs/remotes/origin/main", sha], cwd=repo, check=True)


# --- synthetic-transcript helpers --------------------------------------------


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _assistant(*, branch: str = "feat", ts: str | None = None, content: list | None = None, cwd: str = "/repo") -> dict:
    rec: dict = {
        "type": "assistant", "gitBranch": branch, "isSidechain": False, "cwd": cwd,
        "message": {"content": content or []},
    }
    if ts:
        rec["timestamp"] = ts
    return rec


def _user(text: str, *, branch: str = "feat", ts: str | None = None) -> dict:
    rec: dict = {"type": "user", "gitBranch": branch, "message": {"content": text}}
    if ts:
        rec["timestamp"] = ts
    return rec


def _skill_use(tool_id: str, skill: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}


def _read_use(tool_id: str, path: str) -> dict:
    return {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}


def _agent_use(tool_id: str, subagent_type: str) -> dict:
    return {
        "type": "tool_use", "id": tool_id, "name": "Agent",
        "input": {"subagent_type": subagent_type, "description": "x", "prompt": "y"},
    }


def _write_subagent_dispatch(proj: Path, session_stem: str, agent_id: str, tool_use_id: str, records: list[dict]) -> None:
    subdir = proj / session_stem / "subagents"
    _write_jsonl(subdir / f"{agent_id}.jsonl", records)
    meta = {"agentType": "staff-backend-engineer", "description": "review", "toolUseId": tool_use_id, "spawnDepth": 1}
    (subdir / f"{agent_id}.meta.json").write_text(json.dumps(meta))


# --- Candidate/ConfirmedDefect schema ----------------------------------------


def _candidate_kwargs(**overrides) -> dict:
    kwargs = dict(
        id="c1", source="szz", lens="staff-backend-engineer",
        base_commit=_SHA_A, head_commit=_SHA_B, fix_commit=_SHA_C,
        fix_date="2026-01-01T00:00:00+00:00",
        lines_exist_at_introducing_head=True, reviewer_could_have_caught_it=True,
        file_is_markdown=False,
    )
    kwargs.update(overrides)
    return kwargs


class TestCandidateAndConfirmedDefectSchema:
    def test_unknown_lens_is_rejected(self):
        with pytest.raises(ValueError, match="lens"):
            Candidate(**_candidate_kwargs(lens="staff-product-engineer"))

    def test_non_hex_sha_is_rejected(self):
        with pytest.raises(ValueError, match="SHA"):
            Candidate(**_candidate_kwargs(head_commit="not-a-sha"))

    def test_unknown_source_is_rejected(self):
        with pytest.raises(ValueError, match="source"):
            Candidate(**_candidate_kwargs(source="manual"))

    def test_bad_fix_date_is_rejected(self):
        with pytest.raises(ValueError, match="fix_date"):
            Candidate(**_candidate_kwargs(fix_date="not-a-date"))

    def test_unknown_ref_status_is_rejected(self):
        with pytest.raises(ValueError, match="ref_status"):
            Candidate(**_candidate_kwargs(ref_status="merged"))

    def test_confirmed_defect_with_field_outside_schema_is_rejected(self):
        data = {
            "id": "c1", "source": "szz", "lens": "staff-backend-engineer",
            "base_commit": _SHA_A, "head_commit": _SHA_B, "fix_commit": _SHA_C,
            "fix_date": "2026-01-01T00:00:00+00:00", "description": "fixed a bug",
            "extra_field": "not allowed",
        }
        with pytest.raises(ValueError, match="outside the schema"):
            ConfirmedDefect.from_dict(data)

    def test_confirmed_defect_missing_a_field_is_rejected(self):
        data = {
            "id": "c1", "source": "szz", "lens": "staff-backend-engineer",
            "base_commit": _SHA_A, "head_commit": _SHA_B, "fix_commit": _SHA_C,
            "fix_date": "2026-01-01T00:00:00+00:00",
        }
        with pytest.raises(ValueError, match="missing"):
            ConfirmedDefect.from_dict(data)


class TestGuessLens:
    @pytest.mark.parametrize("path", [
        "claude/.claude/hooks/deny-example.sh",
        "claude/.claude/scripts/tests/test_example.py",
        "claude/.claude/hooks/deny-credential-file-reads.sh",
        "docs/design-decisions.md",
        "evals/review_bench/defects.py",
    ])
    def test_always_returns_a_known_lens(self, path):
        """guess_lens's return value always validates against Candidate's
        own lens check -- an unrecognized guess would crash mining."""
        assert defects.guess_lens(path) in defects.KNOWN_LENSES


class TestJsonLoadSave:
    def test_round_trips_candidates(self, tmp_path):
        path = tmp_path / "candidates.json"
        candidate = Candidate(**_candidate_kwargs(evidence={"path": "app.py"}))
        defects.save_candidates(path, [candidate])
        assert defects.load_candidates(path) == [candidate]

    def test_round_trips_confirmed_defects(self, tmp_path):
        path = tmp_path / "defects.json"
        defect = ConfirmedDefect(
            id="c1", source="szz", lens="staff-backend-engineer",
            base_commit=_SHA_A, head_commit=_SHA_B, fix_commit=_SHA_C,
            fix_date="2026-01-01T00:00:00+00:00", description="a public-git description",
        )
        defects.save_confirmed_defects(path, [defect])
        assert defects.load_confirmed_defects(path) == [defect]

    def test_load_missing_file_returns_empty_list(self, tmp_path):
        assert defects.load_candidates(tmp_path / "missing.json") == []
        assert defects.load_confirmed_defects(tmp_path / "missing.json") == []


class TestAssertUniqueIds:
    def test_duplicate_id_raises_loudly(self):
        candidates = [Candidate(**_candidate_kwargs(id="dup")), Candidate(**_candidate_kwargs(id="dup"))]
        with pytest.raises(ValueError, match="duplicate"):
            defects.assert_unique_ids(candidates, miner="mine-szz")

    def test_all_unique_ids_does_not_raise(self):
        candidates = [Candidate(**_candidate_kwargs(id="c-1")), Candidate(**_candidate_kwargs(id="c-2"))]
        defects.assert_unique_ids(candidates, miner="mine-szz")  # must not raise


class TestPublicGitText:
    def test_concatenates_git_show_of_both_commits(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, "introduce x is one")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x should be two")

        text = defects.public_git_text(repo, introducing_sha, fix_sha)
        assert "introduce x is one" in text
        assert "fix x should be two" in text

    def test_raises_when_the_injected_run_fails(self, tmp_path):
        """A failing run (e.g. an unreachable commit from rewritten history)
        must propagate, not be swallowed here -- cmd_confirm is the layer
        that decides whether to skip just this one candidate."""
        def failing_run(*args, **kwargs):
            raise subprocess.CalledProcessError(returncode=128, cmd=args)

        repo = tmp_path / "repo"  # never touched -- failing_run never calls real git
        with pytest.raises(subprocess.CalledProcessError):
            defects.public_git_text(repo, _SHA_A, _SHA_B, run=failing_run)


class TestCheckDescriptionProvenance:
    _EXCERPT = "the reviewer found that error handling silently swallows the exception"

    def test_description_copying_a_six_word_run_from_an_excerpt_is_rejected(self):
        description = "The error handling silently swallows the exception in this path."
        violation = defects.check_description_provenance(description, "", {"cand-1": self._EXCERPT})
        assert violation is not None
        assert violation.source_candidate_id == "cand-1"
        assert violation.shared_run == "error handling silently swallows the exception"

    def test_description_under_six_tokens_verbatim_quoting_a_short_excerpt_is_rejected(self):
        """Regression: _six_grams returned [] for text under the six-token
        window, and the caller treated an empty list as "no violation" --
        so any five-word-or-shorter description bypassed the leak check
        unconditionally, even one verbatim-quoting a short excerpt."""
        description = "silently swallows the exception here"  # 5 tokens
        excerpt = "the reviewer found that silently swallows the exception here today"
        violation = defects.check_description_provenance(description, "", {"cand-1": excerpt})
        assert violation is not None
        assert violation.source_candidate_id == "cand-1"
        assert violation.shared_run == "silently swallows the exception here"

    def test_six_word_run_also_in_public_git_text_passes(self):
        description = "The error handling silently swallows the exception in this path."
        public_text = "before the fix, error handling silently swallows the exception unconditionally"
        violation = defects.check_description_provenance(description, public_text, {"cand-1": self._EXCERPT})
        assert violation is None

    def test_description_sharing_only_identifiers_and_code_passes(self):
        excerpt = "the `_normalize_cited_path` helper returns None for an unresolvable candidate"
        description = "`_normalize_cited_path` returned None when the candidate path could not resolve at all"
        violation = defects.check_description_provenance(description, "", {"cand-1": excerpt})
        assert violation is None

    def test_non_ascii_six_word_run_is_rejected(self):
        excerpt = "el manejo de errores descartó la excepción silenciosamente aquí"
        description = "El manejo de errores descartó la excepción silenciosamente en este caso."
        violation = defects.check_description_provenance(description, "", {"cand-1": excerpt})
        assert violation is not None

    def test_checked_against_every_candidates_excerpt_not_only_its_own(self):
        description = "The error handling silently swallows the exception in this path."
        violation = defects.check_description_provenance(
            description, "", {"unrelated-candidate": self._EXCERPT},
        )
        assert violation is not None
        assert violation.source_candidate_id == "unrelated-candidate"


# --- mine_szz -----------------------------------------------------------------


class TestMineSzz:
    def test_removed_line_blames_to_its_introducer(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "def f():\n    pass\n")
        _commit(repo, "seed")
        _write(repo, "app.py", "def f():\n    return bad_value\n")
        introducing_sha = _commit(repo, "add f")
        _write(repo, "app.py", "def f():\n    return good_value\n")
        fix_sha = _commit(repo, "fix wrong return value")
        _set_origin_main(repo, fix_sha)

        candidates = mine_szz.mine(repo, base_ref="origin/main")
        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.head_commit == introducing_sha
        assert candidate.fix_commit == fix_sha
        assert candidate.evidence["low_confidence"] is False

    def test_blank_comment_and_markdown_lines_are_ignored(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n\n# a comment\n")
        _write(repo, "README.md", "# Title\nOld line\n")
        _commit(repo, "seed")
        _write(repo, "app.py", "x = 1\n")  # removes the blank line and the comment line
        _write(repo, "README.md", "# Title\n")  # removes a markdown line
        fix_sha = _commit(repo, "fix cleanup")
        _set_origin_main(repo, fix_sha)

        assert mine_szz.mine(repo, base_ref="origin/main") == []

    def test_addition_only_hunk_is_flagged_low_confidence(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "line0\n")
        _commit(repo, "seed")
        _write(repo, "app.py", "line0\nline1\nline2\nline3\n")
        introducing_sha = _commit(repo, "add lines")
        _write(repo, "app.py", "line0\nline1\nline2\nline3\nnew_line\n")
        fix_sha = _commit(repo, "fix: add missing validation")
        _set_origin_main(repo, fix_sha)

        candidates = mine_szz.mine(repo, base_ref="origin/main")
        assert len(candidates) == 1
        assert candidates[0].evidence["low_confidence"] is True
        assert candidates[0].head_commit == introducing_sha

    def test_root_commit_introducer_is_skipped_not_crashed(self, tmp_path):
        """Regression: an introducing commit with no parent (the repo's own
        root commit) previously crashed the whole mining sweep with an
        uncaught CalledProcessError from `git rev-parse <sha>^` -- there is
        no fixture base for a commit with no parent, so it must be skipped."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "def f():\n    return bad_value\n")
        _commit(repo, "add f")  # the repo's own root commit -- no parent exists
        _write(repo, "app.py", "def f():\n    return good_value\n")
        fix_sha = _commit(repo, "fix wrong return value")
        _set_origin_main(repo, fix_sha)

        assert mine_szz.mine(repo, base_ref="origin/main") == []

    def test_non_fix_subject_is_not_mined(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        _commit(repo, "seed")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "add a new feature")  # matches none of fix|bug|regression
        _set_origin_main(repo, fix_sha)

        assert mine_szz.mine(repo, base_ref="origin/main") == []

    def test_two_files_with_same_basename_in_different_dirs_get_distinct_ids(self, tmp_path):
        """Regression: Candidate.id keyed on the changed file's basename
        only, so a fix commit touching two same-named files in different
        directories (e.g. two __init__.py additions) collided on the same
        id despite being distinct candidates."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "pkg_a/__init__.py", "x = 1\n")
        _write(repo, "pkg_b/__init__.py", "y = 1\n")
        _commit(repo, "seed")
        _write(repo, "pkg_a/__init__.py", "x = bad_value\n")
        _write(repo, "pkg_b/__init__.py", "y = bad_value\n")
        _commit(repo, "add values")
        _write(repo, "pkg_a/__init__.py", "x = good_value\n")
        _write(repo, "pkg_b/__init__.py", "y = good_value\n")
        fix_sha = _commit(repo, "fix wrong values")
        _set_origin_main(repo, fix_sha)

        candidates = mine_szz.mine(repo, base_ref="origin/main")
        assert len(candidates) == 2
        assert len({c.id for c in candidates}) == 2

    def test_git_call_failures_are_counted_and_reported(self, tmp_path, capsys):
        """A root-commit introducer's rev-parse failure is a _GIT_CALL_ERRORS
        catch inside _build_candidate -- it must be counted and printed to
        stderr, mirroring mine_review_rounds.mine()'s own ref_status_counts/
        skipped_unresolved convention, which mine_szz previously had none of."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "def f():\n    return bad_value\n")
        _commit(repo, "add f")  # root commit -- no parent
        _write(repo, "app.py", "def f():\n    return good_value\n")
        fix_sha = _commit(repo, "fix wrong return value")
        _set_origin_main(repo, fix_sha)

        mine_szz.mine(repo, base_ref="origin/main")

        captured = capsys.readouterr()
        assert "git_call_failures=1" in captured.err


class TestRank:
    def test_orders_by_confidence_introducer_count_then_size(self, tmp_path):
        """_rank's documented priority (Source 1, step 6): modified-line
        (confident) hits before adjacent-line (low-confidence) hits; a
        single introducer before several; files over one Read call first."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "small.py", "x = 1\n")
        _write(repo, "large.py", "y = 1\n" * 20_000)  # bytes // 4 > _READ_CAP_TOKENS
        commit_sha = _commit(repo, "seed")

        large_confident = Candidate(**_candidate_kwargs(
            id="c-large-confident", head_commit=commit_sha,
            evidence={"path": "large.py", "low_confidence": False, "multiple_introducers": False},
        ))
        small_confident = Candidate(**_candidate_kwargs(
            id="c-small-confident", head_commit=commit_sha,
            evidence={"path": "small.py", "low_confidence": False, "multiple_introducers": False},
        ))
        small_multiple = Candidate(**_candidate_kwargs(
            id="c-small-multiple", head_commit=commit_sha,
            evidence={"path": "small.py", "low_confidence": False, "multiple_introducers": True},
        ))
        small_low_confidence = Candidate(**_candidate_kwargs(
            id="c-small-low-confidence", head_commit=commit_sha,
            evidence={"path": "small.py", "low_confidence": True, "multiple_introducers": False},
        ))

        ranked = mine_szz._rank(repo, [small_low_confidence, small_multiple, large_confident, small_confident])

        assert [c.id for c in ranked] == [
            "c-large-confident", "c-small-confident", "c-small-multiple", "c-small-low-confidence",
        ]


class TestBlameFixCommit:
    def test_reused_directly_by_mine_review_rounds(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, "seed")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")

        introducers, is_low_confidence = mine_szz.blame_fix_commit(repo, fix_sha, "app.py")
        assert introducers == frozenset({introducing_sha})
        assert is_low_confidence is False


# --- mine_review_rounds: branch-ref resolution -------------------------------


class TestResolveBranchRef:
    def _repo_with_bare_origin(self, tmp_path):
        bare = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        _commit(repo, "seed")
        _git(repo, "remote", "add", "origin", str(bare))
        _git(repo, "push", "-q", "origin", "main")
        return repo, bare

    def test_pr_head_fetched_from_local_bare_origin_records_fetched(self, tmp_path):
        repo, bare = self._repo_with_bare_origin(tmp_path)
        head_sha = _git(repo, "rev-parse", "HEAD").strip()
        subprocess.run(["git", "update-ref", "refs/pull/7/head", head_sha], cwd=bare, check=True)

        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 7)
        assert ref_status == "fetched"
        assert ref == "refs/review-bench/pr/7"
        assert _git(repo, "rev-parse", ref).strip() == head_sha

    def test_missing_pr_head_records_fetch_failed(self, tmp_path):
        repo, _bare = self._repo_with_bare_origin(tmp_path)
        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 999)
        assert ref_status == "fetch-failed"
        assert ref is None

    def test_no_pr_number_records_pr_unknown(self, tmp_path):
        repo, _bare = self._repo_with_bare_origin(tmp_path)
        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", None)
        assert ref_status == "pr-unknown"
        assert ref is None

    def test_local_branch_takes_precedence_and_fetches_nothing(self, tmp_path):
        repo, bare = self._repo_with_bare_origin(tmp_path)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "app.py", "x = 2\n")
        local_sha = _commit(repo, "local work")
        _git(repo, "checkout", "-q", "main")
        # The stand-in origin's refs/pull/7/head points at a DIFFERENT commit.
        main_sha = _git(repo, "rev-parse", "main").strip()
        subprocess.run(["git", "update-ref", "refs/pull/7/head", main_sha], cwd=bare, check=True)

        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "feat", 7)
        assert ref_status == "local-branch"
        assert ref == "refs/heads/feat"
        assert _git(repo, "rev-parse", ref).strip() == local_sha

        no_pr_ref = subprocess.run(
            ["git", "rev-parse", "--verify", "refs/review-bench/pr/7"], cwd=repo, capture_output=True,
        )
        assert no_pr_ref.returncode != 0


class TestResolvePrNumber:
    """resolve_pr_number's sole `gh`-boundary call, with subprocess.run
    monkeypatched -- unlike every git-boundary function in this module,
    this one previously had no test, mocked or real, of any of its
    branches."""

    def test_well_formed_payload_returns_the_number(self, tmp_path, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout=json.dumps([{"number": 42}]), stderr="")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)
        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") == 42

    def test_empty_payload_returns_none(self, tmp_path, monkeypatch):
        def fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(args, 0, stdout="[]", stderr="")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)
        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") is None

    def test_gh_not_installed_returns_none(self, tmp_path, monkeypatch):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("gh not found on PATH")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)
        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") is None


class TestResolveDefectCommits:
    """resolve_defect_commits (Source 2's commit-resolution core), against
    real-git fixtures rather than the stubbed return value
    TestMineReviewRoundsCandidates uses to isolate the rest of mine()."""

    def _repo_with_branch_and_bug_fix(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "line0\n")
        main_sha = _commit(repo, "seed")
        _set_origin_main(repo, main_sha)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "app.py", "line0\nbad_value\n")
        introducing_sha = _commit(repo, "introduce bad value")
        _write(repo, "app.py", "line0\ngood_value\n")
        fix_sha = _commit(repo, "fix wrong value")
        return repo, main_sha, introducing_sha, fix_sha

    def test_no_commit_touching_path_after_after_ts_is_unresolved(self, tmp_path):
        repo, _main_sha, _introducing_sha, fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)
        fix_ts = corpus._parse_ts(mine_review_rounds._commit_date(repo, fix_sha))

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, branch="feat", raw_path="app.py", after_ts=fix_ts + 1000, pr_number=None,
        )
        assert resolution.ref_status == "local-branch"
        assert resolution.fix_commit is None
        assert resolution.branch_commits  # still records every touching commit

    def test_multiple_introducers_is_unresolved(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "line0\n")
        main_sha = _commit(repo, "seed")
        _set_origin_main(repo, main_sha)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "app.py", "line0\nbad_a\n")
        _commit(repo, "introduce bad a")
        _write(repo, "app.py", "line0\nbad_a\nbad_b\n")
        _commit(repo, "introduce bad b")
        _write(repo, "app.py", "line0\ngood_a\ngood_b\n")
        fix_sha = _commit(repo, "fix wrong values")
        fix_ts = corpus._parse_ts(mine_review_rounds._commit_date(repo, fix_sha))

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, branch="feat", raw_path="app.py", after_ts=fix_ts - 1000, pr_number=None,
        )
        assert resolution.ref_status == "local-branch"
        # An unresolved branch's _CommitResolution carries only ref_status
        # and branch_commits -- fix_commit itself is None too, not just
        # head_commit/base_commit, since every early return before the
        # fully-resolved happy path omits all three commit fields.
        assert resolution.fix_commit is None  # len(introducers) != 1
        assert resolution.head_commit is None
        assert fix_sha in [c["sha"] for c in resolution.branch_commits]  # still records every touching commit

    def test_clean_resolution_returns_expected_commit_values(self, tmp_path):
        repo, main_sha, introducing_sha, fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)
        fix_ts = corpus._parse_ts(mine_review_rounds._commit_date(repo, fix_sha))

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, branch="feat", raw_path="app.py", after_ts=fix_ts - 1000, pr_number=None,
        )
        assert resolution.ref_status == "local-branch"
        assert resolution.head_commit == introducing_sha
        assert resolution.base_commit == main_sha
        assert resolution.fix_commit == fix_sha
        assert resolution.fix_date is not None


# --- mine_review_rounds: session-scope guard ---------------------------------


class TestResolveScopedSessionsRootGuard:
    def test_more_than_one_root_exits_2(self, tmp_path):
        root_a = tmp_path / "a" / "projects"
        root_b = tmp_path / "b" / "projects"
        root_a.mkdir(parents=True)
        root_b.mkdir(parents=True)
        with pytest.raises(SystemExit) as exc_info:
            mine_review_rounds.resolve_scoped_sessions(roots=(root_a, root_b))
        assert exc_info.value.code == 2


# --- mine_review_rounds: round-scope and citation matching -------------------


class TestRoundScopeAndCitations:
    def test_round_scope_collects_main_thread_reads(self):
        cited_path = "/repo/app.py"
        records = [
            _assistant(ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(ts="2026-01-01T00:01:00Z", content=[_read_use("r1", cited_path)]),
            _user("done", ts="2026-01-01T00:02:00Z"),
        ]
        windows = review_rounds._detect_round_windows(records)
        scope_keys = mine_review_rounds._round_scope(records, windows[0], {})
        expected_key = reviewer_yield._normalize_cited_path(cited_path, "/repo")
        assert expected_key in scope_keys
        assert scope_keys[expected_key] == cited_path

    def test_round_scope_collects_reviewer_subagent_reads(self, tmp_path):
        """A round's scope covers Reads by any reviewer-typed subagent it
        dispatched inside the window, not only the main thread's own Reads
        (Approach > Defect set > Source 2, step 3)."""
        cited_path = "/repo/other.py"
        agent_dispatch_id = "toolu_agent9"
        records = [
            _assistant(ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(ts="2026-01-01T00:01:00Z", content=[_agent_use(agent_dispatch_id, "staff-backend-engineer")]),
            _user("done", ts="2026-01-01T00:02:00Z"),
        ]
        jsonl = tmp_path / "sess-1.jsonl"
        _write_jsonl(jsonl, records)
        subagent_records = [_assistant(ts="2026-01-01T00:01:15Z", content=[_read_use("r1", cited_path)])]
        _write_subagent_dispatch(tmp_path, "sess-1", "agent-1", agent_dispatch_id, subagent_records)

        windows = review_rounds._detect_round_windows(records)
        dispatch_index, _meta_errors = corpus._index_subagent_dispatches(jsonl)
        scope_keys = mine_review_rounds._round_scope(records, windows[0], dispatch_index)
        expected_key = reviewer_yield._normalize_cited_path(cited_path, "/repo")
        assert expected_key in scope_keys


class TestMainThreadEditedBetween:
    def test_edit_of_a_line_suffixed_citation_target_is_detected(self):
        """Regression: this comparison used raw string equality between the
        citation's raw_path (which commonly carries a ":line" suffix) and
        the write target's clean path, so a line-suffixed citation --
        exactly the shape TestMineReviewRoundsCandidates' own fixtures use
        -- never matched a real edit of that same file."""
        edit_record = _assistant(
            ts="2026-01-01T12:00:00Z", cwd="/repo",
            content=[{"type": "tool_use", "id": "e1", "name": "Edit", "input": {"file_path": "/repo/app.py"}}],
        )
        records = [
            _assistant(ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            edit_record,
            _assistant(ts="2026-01-02T00:00:00Z", content=[_skill_use("s2", "code-review")]),
        ]
        jsonl = Path("/tmp/sess-main-thread-edited-between.jsonl")
        earlier = mine_review_rounds._RoundEntry(
            jsonl=jsonl, records=records, dispatch_index={}, window=(0, 1, "code-review"), branch="feat", ts=1.0,
        )
        later = mine_review_rounds._RoundEntry(
            jsonl=jsonl, records=records, dispatch_index={}, window=(2, 3, "code-review"), branch="feat", ts=2.0,
        )
        key = reviewer_yield._normalize_cited_path("/repo/app.py:12", "/repo")

        assert mine_review_rounds._main_thread_edited_between(earlier, later, key) is True


class TestMineReviewRoundsCandidates:
    """mine() end-to-end, with resolve_defect_commits stubbed so these tests
    isolate the round-window / round-scope / citation-matching behavior from
    fixture-commit resolution (covered separately by TestResolveBranchRef
    and TestBlameFixCommit)."""

    _CITED_PATH = "/repo/app.py"
    _DUMMY_RESOLUTION = mine_review_rounds._CommitResolution(
        ref_status="local-branch", base_commit=_SHA_A, head_commit=_SHA_B,
        fix_commit=_SHA_C, fix_date="2026-01-01T00:00:00+00:00",
    )

    def _build_session(self, tmp_path, *, branch: str = "feat", finding_text: str | None = None) -> Path:
        proj = tmp_path / "projects" / "test-slug"
        proj.mkdir(parents=True)
        session_stem = "sess-1"
        jsonl = proj / f"{session_stem}.jsonl"

        agent_dispatch_id = "toolu_agent1"
        records = [
            _assistant(branch=branch, ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(branch=branch, ts="2026-01-01T00:01:00Z", content=[_read_use("r1", self._CITED_PATH)]),
            _user("looks fine", branch=branch, ts="2026-01-01T00:02:00Z"),
            _assistant(branch=branch, ts="2026-01-02T00:00:00Z", content=[_skill_use("s2", "code-review")]),
            _assistant(
                branch=branch, ts="2026-01-02T00:01:00Z",
                content=[_agent_use(agent_dispatch_id, "staff-backend-engineer")],
            ),
            _user("thanks", branch=branch, ts="2026-01-02T00:02:00Z"),
        ]
        _write_jsonl(jsonl, records)

        text = finding_text or f"Reviewer finding: {self._CITED_PATH}:12 has a bug in the loop."
        subagent_records = [_assistant(ts="2026-01-02T00:01:30Z", cwd="/repo", content=[{"type": "text", "text": text}])]
        _write_subagent_dispatch(proj, session_stem, "agent-1", agent_dispatch_id, subagent_records)
        return tmp_path

    def _mine(self, tmp_path, monkeypatch):
        monkeypatch.setattr(mine_review_rounds.scope, "_repo_scoped_project_slugs", lambda label: ["test-slug"])
        monkeypatch.setattr(mine_review_rounds, "resolve_pr_number", lambda *a, **k: None)
        monkeypatch.setattr(mine_review_rounds, "resolve_defect_commits", lambda *a, **k: self._DUMMY_RESOLUTION)
        return mine_review_rounds.mine(tmp_path, roots=(tmp_path / "projects",))

    def test_later_round_citation_of_an_earlier_scoped_path_yields_a_candidate(self, tmp_path, monkeypatch):
        self._build_session(tmp_path)
        candidates = self._mine(tmp_path, monkeypatch)
        assert len(candidates) == 1
        # evidence["path"] carries the later round's own raw citation text,
        # including the ":12" line suffix it was quoted with -- only the
        # join *key* (not this raw string) is line-suffix-normalized.
        assert candidates[0].evidence["path"] == f"{self._CITED_PATH}:12"
        assert candidates[0].source == "review-round"

    def test_citation_of_a_path_outside_earlier_scope_yields_no_candidate(self, tmp_path, monkeypatch):
        self._build_session(tmp_path, finding_text="Reviewer finding: /repo/unrelated.py:9 has a bug.")
        candidates = self._mine(tmp_path, monkeypatch)
        assert candidates == []

    def test_citation_on_a_different_branch_yields_no_candidate(self, tmp_path, monkeypatch):
        self._build_session(tmp_path, branch="feat")
        proj = tmp_path / "projects" / "test-slug"
        jsonl = proj / "sess-1.jsonl"
        records = [json.loads(line) for line in jsonl.read_text().splitlines() if line]
        for rec in records[3:]:
            rec["gitBranch"] = "other-branch"
        _write_jsonl(jsonl, records)

        candidates = self._mine(tmp_path, monkeypatch)
        assert candidates == []

    def test_two_later_rounds_citing_the_same_path_get_distinct_ids(self, tmp_path, monkeypatch):
        """Regression: review-round candidate ids had no round-distinguishing
        component, so a second later round citing the same earlier-scoped
        path collided on the same id as the first and silently overwrote
        it in confirm's excerpts_by_id/existing_ids lookups."""
        proj = tmp_path / "projects" / "test-slug"
        proj.mkdir(parents=True)
        session_stem = "sess-1"
        jsonl = proj / f"{session_stem}.jsonl"

        agent_dispatch_id_1 = "toolu_agent1"
        agent_dispatch_id_2 = "toolu_agent2"
        records = [
            _assistant(ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(ts="2026-01-01T00:01:00Z", content=[_read_use("r1", self._CITED_PATH)]),
            _user("looks fine", ts="2026-01-01T00:02:00Z"),
            _assistant(ts="2026-01-02T00:00:00Z", content=[_skill_use("s2", "code-review")]),
            _assistant(ts="2026-01-02T00:01:00Z", content=[_agent_use(agent_dispatch_id_1, "staff-backend-engineer")]),
            _user("thanks", ts="2026-01-02T00:02:00Z"),
            _assistant(ts="2026-01-03T00:00:00Z", content=[_skill_use("s3", "code-review")]),
            _assistant(ts="2026-01-03T00:01:00Z", content=[_agent_use(agent_dispatch_id_2, "staff-backend-engineer")]),
            _user("thanks again", ts="2026-01-03T00:02:00Z"),
        ]
        _write_jsonl(jsonl, records)

        finding_text = f"Reviewer finding: {self._CITED_PATH}:12 has a bug in the loop."
        finding_block = [{"type": "text", "text": finding_text}]
        subagent_records_1 = [_assistant(ts="2026-01-02T00:01:30Z", cwd="/repo", content=finding_block)]
        _write_subagent_dispatch(proj, session_stem, "agent-1", agent_dispatch_id_1, subagent_records_1)
        subagent_records_2 = [_assistant(ts="2026-01-03T00:01:30Z", cwd="/repo", content=finding_block)]
        _write_subagent_dispatch(proj, session_stem, "agent-2", agent_dispatch_id_2, subagent_records_2)

        candidates = self._mine(tmp_path, monkeypatch)
        assert len(candidates) == 2
        assert len({c.id for c in candidates}) == 2

    def test_one_later_round_matching_two_earlier_rounds_gets_distinct_ids(self, tmp_path, monkeypatch):
        """Regression: review-round candidate ids carried only the later
        round's timestamp, so a later round whose citation matched more than
        one earlier round's scope on the same path collided on the same id
        despite carrying distinct earlier_round_ts/main_thread_edited_between
        evidence."""
        proj = tmp_path / "projects" / "test-slug"
        proj.mkdir(parents=True)
        session_stem = "sess-1"
        jsonl = proj / f"{session_stem}.jsonl"

        agent_dispatch_id = "toolu_agent1"
        records = [
            _assistant(ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(ts="2026-01-01T00:01:00Z", content=[_read_use("r1", self._CITED_PATH)]),
            _user("looks fine", ts="2026-01-01T00:02:00Z"),
            _assistant(ts="2026-01-02T00:00:00Z", content=[_skill_use("s2", "code-review")]),
            _assistant(ts="2026-01-02T00:01:00Z", content=[_read_use("r2", self._CITED_PATH)]),
            _user("still fine", ts="2026-01-02T00:02:00Z"),
            _assistant(ts="2026-01-03T00:00:00Z", content=[_skill_use("s3", "code-review")]),
            _assistant(ts="2026-01-03T00:01:00Z", content=[_agent_use(agent_dispatch_id, "staff-backend-engineer")]),
            _user("thanks", ts="2026-01-03T00:02:00Z"),
        ]
        _write_jsonl(jsonl, records)

        finding_text = f"Reviewer finding: {self._CITED_PATH}:12 has a bug in the loop."
        subagent_records = [_assistant(ts="2026-01-03T00:01:30Z", cwd="/repo", content=[{"type": "text", "text": finding_text}])]
        _write_subagent_dispatch(proj, session_stem, "agent-1", agent_dispatch_id, subagent_records)

        candidates = self._mine(tmp_path, monkeypatch)
        assert len(candidates) == 2
        assert len({c.id for c in candidates}) == 2


# --- confirm CLI --------------------------------------------------------------


def _confirm_args(local_dir: Path, defects_path: Path):
    return run_review_bench.build_parser().parse_args(
        ["confirm", "--local-dir", str(local_dir), "--defects-path", str(defects_path)]
    )


class TestConfirmCli:
    def _repo_with_two_commits(self, tmp_path) -> tuple[Path, str, str]:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        return repo, introducing_sha, fix_sha

    def test_rejection_prints_only_id_six_gram_and_source_candidate_id(self, tmp_path, monkeypatch, capsys):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        local_dir = tmp_path / "local"
        excerpt = "the reviewer found that error handling silently swallows the exception"
        source_candidate = Candidate(**_candidate_kwargs(
            id="src-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            excerpt=excerpt,
        ))
        candidate_to_confirm = Candidate(**_candidate_kwargs(
            id="c-2", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="The error handling silently swallows the exception in x.",
        ))
        defects.save_candidates(local_dir / "szz_candidates.json", [source_candidate, candidate_to_confirm])
        defects_path = tmp_path / "defects.json"

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        captured = capsys.readouterr()
        assert "c-2" in captured.err
        assert "error handling silently swallows the exception" in captured.err
        assert "src-1" in captured.err
        assert "reviewer found that" not in captured.err  # no other excerpt text leaks
        assert defects.load_confirmed_defects(defects_path) == []

    def test_confirm_appends_passing_candidate_and_is_idempotent_on_rerun(self, tmp_path, monkeypatch):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        local_dir = tmp_path / "local"
        candidate = Candidate(**_candidate_kwargs(
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        ))
        defects.save_candidates(local_dir / "szz_candidates.json", [candidate])
        defects_path = tmp_path / "defects.json"
        args = _confirm_args(local_dir, defects_path)

        run_review_bench.cmd_confirm(args)
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]

        run_review_bench.cmd_confirm(args)  # retry -- must not duplicate
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]

    def test_candidate_with_no_description_is_not_promoted(self, tmp_path, monkeypatch):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        local_dir = tmp_path / "local"
        candidate = Candidate(**_candidate_kwargs(
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
        ))
        defects.save_candidates(local_dir / "szz_candidates.json", [candidate])
        defects_path = tmp_path / "defects.json"

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))
        assert defects.load_confirmed_defects(defects_path) == []

    def test_confirm_skips_one_unresolvable_candidate_but_processes_the_rest(self, tmp_path, monkeypatch):
        """Regression: public_git_text's `git show` (check=True) had no
        try/except at its cmd_confirm call site, so one candidate whose
        commits are unreachable (rewritten history, a stale .local/
        shortlist) crashed confirmation for every other candidate in the
        same run."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        local_dir = tmp_path / "local"
        bad_candidate = Candidate(**_candidate_kwargs(
            id="c-bad", base_commit=introducing_sha, head_commit=_SHA_A, fix_commit=_SHA_B,
            description="a commit that no longer exists in this repository's history.",
        ))
        good_candidate = Candidate(**_candidate_kwargs(
            id="c-good", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        ))
        defects.save_candidates(local_dir / "szz_candidates.json", [bad_candidate, good_candidate])
        defects_path = tmp_path / "defects.json"

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-good"]

    def test_confirm_aborts_without_overwriting_when_defects_file_changed_concurrently(self, tmp_path, monkeypatch):
        """Regression: cmd_confirm's read-modify-write of the committed
        defects.json re-derived existing + appended from a stale read, so
        an overlapping confirm run's own append was silently discarded."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        local_dir = tmp_path / "local"
        candidate = Candidate(**_candidate_kwargs(
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        ))
        defects.save_candidates(local_dir / "szz_candidates.json", [candidate])
        defects_path = tmp_path / "defects.json"

        original_load = defects.load_confirmed_defects
        mutated = {"done": False}

        def load_then_mutate_once(path):
            result = original_load(path)
            if not mutated["done"]:
                mutated["done"] = True
                concurrent_defect = ConfirmedDefect(
                    id="concurrent", source="szz", lens="staff-backend-engineer",
                    base_commit=_SHA_A, head_commit=_SHA_B, fix_commit=_SHA_C,
                    fix_date="2026-01-01T00:00:00+00:00", description="a concurrently appended defect.",
                )
                defects.save_confirmed_defects(path, [*result, concurrent_defect])
            return result

        monkeypatch.setattr(defects, "load_confirmed_defects", load_then_mutate_once)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 1
        ids = {d.id for d in original_load(defects_path)}
        assert ids == {"concurrent"}  # this run's own append must not overwrite it
