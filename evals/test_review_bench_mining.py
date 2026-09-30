"""Tests for evals/review_bench's defect schema, miners, and confirmation
CLI. Offline throughout: mine_szz and mine_review_rounds
fixtures are real tmp-path git repos (a local bare repo standing in for
`origin` where a PR-head fetch is exercised) and synthetic transcript
JSONL. No test launches `claude`.
"""
from __future__ import annotations

import io
import json
import os
import signal
import subprocess
import sys
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


def _commit_at(repo: Path, message: str, iso_date: str) -> str:
    """Commit with both git dates pinned, so branch history has a known
    timeline relative to review-round timestamps."""
    _git(repo, "add", "-A")
    subprocess.run(
        ["git", "commit", "-q", "-m", message], cwd=repo, capture_output=True, check=True,
        env={**os.environ, "GIT_AUTHOR_DATE": iso_date, "GIT_COMMITTER_DATE": iso_date},
    )
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

    @pytest.mark.parametrize("malformed_sha", [_SHA_B + "\n", "\n" + _SHA_B, _SHA_B + "0", _SHA_B[:-1]])
    def test_a_sha_with_a_trailing_newline_or_the_wrong_length_is_rejected(self, malformed_sha):
        with pytest.raises(ValueError, match="SHA"):
            Candidate(**_candidate_kwargs(head_commit=malformed_sha))

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

    @pytest.mark.parametrize(("path", "expected_lens"), [
        ("scripts/build.sh", "staff-platform-engineer"),
        ("claude/.claude/hooks/example.py", "staff-platform-engineer"),
        ("claude/.claude/scripts/test_example.py", "staff-sdet"),
        ("claude/.claude/scripts/tests/helper.py", "staff-sdet"),
        ("claude/.claude/scripts/permission_check.py", "ciso-reviewer"),
        ("src/auth/session.py", "ciso-reviewer"),
        ("docs/design-decisions.md", "comment-discipline-reviewer"),
        ("evals/review_bench/defects.py", "staff-backend-engineer"),
    ])
    def test_each_path_shape_maps_to_its_own_lens(self, path, expected_lens):
        assert defects.guess_lens(path) == expected_lens


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


class TestCommitSubjects:
    def test_returns_each_distinct_commit_once_in_the_order_given(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        first_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        second_sha = _commit(repo, "fix x value")

        assert defects.commit_subjects(repo, [second_sha, first_sha, second_sha]) == [
            (second_sha, "fix x value"), (first_sha, "introduce x"),
        ]

    def test_keeps_a_carriage_return_and_a_unicode_line_separator_inside_the_subject(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        sha = _commit(repo, "introduce\rx\N{LINE SEPARATOR}now")

        assert defects.commit_subjects(repo, [sha]) == [(sha, "introduce\rx\N{LINE SEPARATOR}now")]

    def test_raises_for_a_commit_the_repository_lacks(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        _commit(repo, "introduce x")

        with pytest.raises(subprocess.CalledProcessError):
            defects.commit_subjects(repo, [_SHA_A])

    @pytest.mark.parametrize("not_a_sha", ["--output=/tmp/x", "HEAD", "abc123", ""])
    def test_rejects_anything_but_a_full_sha_before_running_git(self, tmp_path, not_a_sha):
        def fail_if_git_runs(*args, **kwargs):
            raise AssertionError("git must not run for a non-SHA commit")

        with pytest.raises(ValueError, match="40-hex"):
            defects.commit_subjects(tmp_path, [not_a_sha], run=fail_if_git_runs)


class TestCheckDescriptionProvenance:
    _EXCERPT = "the reviewer found that error handling silently swallows the exception"

    def test_description_copying_a_six_word_run_from_an_excerpt_is_rejected(self):
        description = "The error handling silently swallows the exception in this path."
        violation = defects.check_description_provenance(description, "", {"cand-1": self._EXCERPT})
        assert violation is not None
        assert violation.source_candidate_id == "cand-1"
        assert violation.shared_run == "error handling silently swallows the exception"

    def test_description_under_six_tokens_verbatim_quoting_a_short_excerpt_is_rejected(self):
        """A description under the six-token window still must be checked for
        verbatim-quoting a short excerpt, not waved through as "no violation"."""
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
        """Only the code-line removal in util.py is mined; the blank and comment
        lines removed from app.py and the line removed from README.md are not."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "notes.txt", "unrelated\n")
        _commit(repo, "seed")  # root commit: holds none of the lines under test
        _write(repo, "app.py", "x = 1\n\n# a comment\n")
        _write(repo, "README.md", "# Title\nOld line\n")
        _write(repo, "util.py", "keep = 1\nbad_call()\n")
        introducing_sha = _commit(repo, "add files")
        _write(repo, "app.py", "x = 1\n")  # removes the blank line and the comment line
        _write(repo, "README.md", "# Title\n")  # removes a markdown line
        _write(repo, "util.py", "keep = 1\n")  # removes a code line
        fix_sha = _commit(repo, "fix cleanup")
        _set_origin_main(repo, fix_sha)

        candidates = mine_szz.mine(repo, base_ref="origin/main")
        assert [(c.evidence["path"], c.head_commit) for c in candidates] == [("util.py", introducing_sha)]

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
        """An introducing commit with no parent (the repo's own root commit) has
        no fixture base and must be skipped, not crashed on."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "def f():\n    return bad_value\n")
        _commit(repo, "add f")  # the repo's own root commit -- no parent exists
        _write(repo, "app.py", "def f():\n    return good_value\n")
        fix_sha = _commit(repo, "fix wrong return value")
        _set_origin_main(repo, fix_sha)

        assert mine_szz.mine(repo, base_ref="origin/main") == []

    def test_non_fix_subject_is_not_mined(self, tmp_path):
        """The removed line has a real introducer (see
        test_removed_line_blames_to_its_introducer), so only the commit
        subject keeps this change from being mined."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "notes.txt", "unrelated\n")
        _commit(repo, "seed")
        _write(repo, "app.py", "x = 1\n")
        _commit(repo, "add x")
        _write(repo, "app.py", "x = 2\n")
        feature_sha = _commit(repo, "add a new feature")  # matches none of fix|bug|regression
        _set_origin_main(repo, feature_sha)

        assert mine_szz.mine(repo, base_ref="origin/main") == []

    def test_two_files_with_same_basename_in_different_dirs_get_distinct_ids(self, tmp_path):
        """Candidate.id must key on the changed file's full path, not just its
        basename, so a fix commit touching two same-named files in different
        directories (e.g. two __init__.py additions) gets distinct ids."""
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
        skipped_unresolved convention."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "def f():\n    return bad_value\n")
        _commit(repo, "add f")  # root commit -- no parent
        _write(repo, "app.py", "def f():\n    return good_value\n")
        fix_sha = _commit(repo, "fix wrong return value")
        _set_origin_main(repo, fix_sha)

        mine_szz.mine(repo, base_ref="origin/main")

        captured = capsys.readouterr()
        assert "git_call_failures=1" in captured.err


class TestParseUnifiedDiff:
    def test_removed_lines_starting_with_dashes_stay_in_their_hunk(self):
        """A removed line whose own text begins `--` (a SQL/Lua comment, a
        YAML fence) renders as `---...` and must not read as a file header."""
        diff_text = "\n".join([
            "diff --git a/q.sql b/q.sql",
            "--- a/q.sql",
            "+++ b/q.sql",
            "@@ -1,2 +1,2 @@",
            "--- note",
            "-select 1",
            "+++ added",
            "+select 2",
            "@@ -9 +9 @@",
            "----",
            "\\ No newline at end of file",
            "+fence",
            "diff --git a/other.py b/other.py",
            "--- a/other.py",
            "+++ b/other.py",
            "@@ -4 +4 @@",
            "-old",
            "+new",
        ])

        files = mine_szz._parse_unified_diff(diff_text)

        assert [fd.old_path for fd in files] == ["q.sql", "other.py"]
        assert [h.removed_lines for h in files[0].hunks] == [["-- note", "select 1"], ["---"]]
        assert files[1].hunks[0].removed_lines == ["old"]


class TestMineSzzDegradesInsteadOfRaising:
    def test_removed_double_dash_comment_does_not_break_blame_of_the_real_removal(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "q.sql", "select 0;\n")
        _commit(repo, "seed")
        _write(repo, "q.sql", "-- note\nselect bad;\n")
        introducing_sha = _commit(repo, "add query")
        _write(repo, "q.sql", "select good;\n")
        fix_sha = _commit(repo, "fix wrong query")
        _set_origin_main(repo, fix_sha)

        candidates = mine_szz.mine(repo, base_ref="origin/main")

        assert [(c.head_commit, c.fix_commit) for c in candidates] == [(introducing_sha, fix_sha)]

    def test_blame_failure_for_one_file_drops_it_counts_it_and_keeps_the_others(self, tmp_path, monkeypatch, capsys):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "a.py", "x = 0\n")
        _write(repo, "b.py", "y = 0\n")
        _commit(repo, "seed")
        _write(repo, "a.py", "x = bad_value\n")
        _write(repo, "b.py", "y = bad_value\n")
        introducing_sha = _commit(repo, "add values")
        _write(repo, "a.py", "x = good_value\n")
        _write(repo, "b.py", "y = good_value\n")
        fix_sha = _commit(repo, "fix wrong values")
        _set_origin_main(repo, fix_sha)
        real_run_git = mine_szz._run_git

        def run_git_failing_blame_of_a(args, *, cwd):
            if args[0] == "blame" and args[-1] == "a.py":
                raise subprocess.TimeoutExpired(cmd="git blame", timeout=1.0)
            return real_run_git(args, cwd=cwd)

        monkeypatch.setattr(mine_szz, "_run_git", run_git_failing_blame_of_a)

        candidates = mine_szz.mine(repo, base_ref="origin/main")

        assert [(c.evidence["path"], c.head_commit, c.fix_commit) for c in candidates] == [
            ("b.py", introducing_sha, fix_sha),
        ]
        assert "git_call_failures=1" in capsys.readouterr().err


class TestRank:
    def test_orders_by_confidence_introducer_count_then_size(self, tmp_path):
        """_rank's documented priority: modified-line (confident) hits
        before adjacent-line (low-confidence) hits; a single introducer
        before several; files over one Read call first."""
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

    def test_force_pushed_pr_head_is_fetched_again_on_a_re_mine(self, tmp_path):
        repo, bare = self._repo_with_bare_origin(tmp_path)
        first_head_sha = _git(repo, "rev-parse", "HEAD").strip()
        subprocess.run(["git", "update-ref", "refs/pull/7/head", first_head_sha], cwd=bare, check=True)
        assert mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 7) == (
            "fetched", "refs/review-bench/pr/7",
        )
        _git(repo, "commit", "--amend", "-q", "-m", "rewritten seed")  # a head that is not a descendant
        rewritten_head_sha = _git(repo, "rev-parse", "HEAD").strip()
        _git(repo, "push", "-q", "--force", "origin", "HEAD:refs/pull/7/head")

        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 7)

        assert ref_status == "fetched"
        assert _git(repo, "rev-parse", ref).strip() == rewritten_head_sha
        assert rewritten_head_sha != first_head_sha

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
    """resolve_pr_number is the module's only `gh`-boundary call, with
    subprocess.run monkeypatched in place of the real binary."""

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

    def test_gh_call_failure_returns_none_and_logs_stderr(self, tmp_path, monkeypatch, capsys):
        def fake_run(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args, stderr="rate limited")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)
        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") is None

        captured = capsys.readouterr()
        assert "rate limited" in captured.err


def _ts(iso_date: str) -> float:
    ts = corpus._parse_ts(iso_date)
    assert ts is not None
    return ts


def _open_feat_branch(repo: Path) -> mine_review_rounds._BranchGit:
    return mine_review_rounds.open_branch_git(repo, mine_review_rounds.resolve_branch_ref(repo, "feat", None))


class TestResolveDefectCommits:
    """resolve_defect_commits (mine_review_rounds.py's own commit-resolution
    core), against real-git fixtures rather than the stubbed return value
    TestMineReviewRoundsCandidates uses to isolate the rest of mine()."""

    def _repo_with_branch_and_bug_fix(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "line0\n")
        main_sha = _commit_at(repo, "seed", "2026-01-01T00:00:00+0000")
        _set_origin_main(repo, main_sha)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "app.py", "line0\nbad_value\n")
        introducing_sha = _commit_at(repo, "introduce bad value", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", "line0\ngood_value\n")
        fix_sha = _commit_at(repo, "fix wrong value", "2026-01-04T00:00:00+0000")
        return repo, main_sha, introducing_sha, fix_sha

    def test_no_commit_touching_path_after_after_ts_is_unresolved(self, tmp_path):
        repo, _main_sha, _introducing_sha, _fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, path="app.py", after_ts=_ts("2026-01-05T00:00:00Z"), branch=_open_feat_branch(repo),
        )
        assert resolution.ref_status == "local-branch"
        assert resolution.fix_commit is None
        assert resolution.branch_commits  # still records the touching commits

    def test_multiple_introducers_is_unresolved(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "line0\n")
        main_sha = _commit_at(repo, "seed", "2026-01-01T00:00:00+0000")
        _set_origin_main(repo, main_sha)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, "app.py", "line0\nbad_a\n")
        _commit_at(repo, "introduce bad a", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", "line0\nbad_a\nbad_b\n")
        _commit_at(repo, "introduce bad b", "2026-01-03T00:00:00+0000")
        _write(repo, "app.py", "line0\ngood_a\ngood_b\n")
        fix_sha = _commit_at(repo, "fix wrong values", "2026-01-05T00:00:00+0000")

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, path="app.py", after_ts=_ts("2026-01-04T00:00:00Z"), branch=_open_feat_branch(repo),
        )
        assert resolution.ref_status == "local-branch"
        # An unresolved branch's _CommitResolution carries ref_status and
        # branch_commits; fix_commit is None too, not just head_commit.
        assert resolution.fix_commit is None  # len(introducers) != 1
        assert resolution.head_commit is None
        assert fix_sha in [c["sha"] for c in resolution.branch_commits]  # the fix commit is among the recorded commits

    def test_clean_resolution_returns_expected_commit_values(self, tmp_path):
        repo, main_sha, introducing_sha, fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, path="app.py", after_ts=_ts("2026-01-03T00:00:00Z"), branch=_open_feat_branch(repo),
        )
        assert resolution.ref_status == "local-branch"
        assert resolution.head_commit == introducing_sha
        assert resolution.base_commit == main_sha
        assert resolution.fix_commit == fix_sha
        assert resolution.fix_date is not None

    def test_earliest_commit_after_the_round_is_the_fix_when_several_follow_it(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)
        _write(repo, "app.py", "line0\nbetter_value\n")
        _commit_at(repo, "rework value again", "2026-01-06T00:00:00+0000")

        resolution = mine_review_rounds.resolve_defect_commits(
            repo, path="app.py", after_ts=_ts("2026-01-03T00:00:00Z"), branch=_open_feat_branch(repo),
        )
        assert resolution.fix_commit == fix_sha
        assert resolution.head_commit == introducing_sha  # the later rework would blame to the fix instead

    def test_repeated_and_equivalent_requests_blame_once(self, tmp_path, monkeypatch):
        repo, _main_sha, _introducing_sha, _fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)
        blame_calls: list[str] = []
        real_blame_fix_commit = mine_review_rounds.mine_szz.blame_fix_commit

        def counting_blame_fix_commit(repo_dir, fix_commit, path, **kwargs):
            blame_calls.append(fix_commit)
            return real_blame_fix_commit(repo_dir, fix_commit, path, **kwargs)

        monkeypatch.setattr(mine_review_rounds.mine_szz, "blame_fix_commit", counting_blame_fix_commit)
        branch = _open_feat_branch(repo)

        for after_iso in ("2026-01-03T00:00:00Z", "2026-01-03T00:00:00Z", "2026-01-02T12:00:00Z"):
            mine_review_rounds.resolve_defect_commits(repo, path="app.py", after_ts=_ts(after_iso), branch=branch)

        assert len(blame_calls) == 1  # two distinct round times, same earliest fix commit

    @pytest.mark.parametrize("ref_status", ["pr-unknown", "fetch-failed"])
    def test_ref_is_none_returns_bare_resolution_without_touching_git(self, tmp_path, ref_status):
        """resolve_branch_ref returns a None ref for both pr-unknown (no PR
        found) and fetch-failed (PR head fetch errored) -- either way
        resolve_defect_commits must short-circuit before its first git call
        rather than crash on an unresolvable ref. Uses the class's real-git
        fixture, not a bare tmp_path, so a mutant substituting a fallback ref
        for the short-circuit would resolve real commits and fail the
        all-None assertions below."""
        repo, _main_sha, _introducing_sha, _fix_sha = self._repo_with_branch_and_bug_fix(tmp_path)
        branch = mine_review_rounds.open_branch_git(repo, (ref_status, None))
        resolution = mine_review_rounds.resolve_defect_commits(repo, path="app.py", after_ts=0.0, branch=branch)
        assert resolution.ref_status == ref_status
        assert resolution.base_commit is None
        assert resolution.head_commit is None
        assert resolution.fix_commit is None
        assert resolution.fix_date is None
        assert resolution.branch_commits == []


class TestMatchRepoPath:
    _TOUCHED = frozenset({"evals/review_bench/runner.py", "runner.py", "docs/a/notes.md", "docs/b/notes.md"})

    @pytest.mark.parametrize(
        ("citation", "expected"),
        [
            ("evals/review_bench/runner.py", "evals/review_bench/runner.py"),
            ("/work/repo/.claude/worktrees/GH-1/my-slug/evals/review_bench/runner.py:42", "evals/review_bench/runner.py"),
            ("~/repo/evals/review_bench/runner.py:42:7", "evals/review_bench/runner.py"),
            ("/work/repo/runner.py:3", "runner.py"),
            ("./evals/review_bench/runner.py", "evals/review_bench/runner.py"),
            ("review_bench/runner.py", "evals/review_bench/runner.py"),
            ("/work/repo/evals/review_bench/other.py:9", None),
            ("a/notes.md", "docs/a/notes.md"),
            ("notes/a.md", None),
        ],
    )
    def test_citation_maps_to_the_longest_matching_touched_path(self, citation, expected):
        assert mine_review_rounds.match_repo_path(citation, self._TOUCHED) == expected

    def test_relative_citation_matching_several_touched_paths_is_unresolved(self):
        touched = frozenset({"docs/a/notes.md", "other/a/notes.md"})
        assert mine_review_rounds.match_repo_path("a/notes.md", touched) is None


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
        windows = review_rounds.detect_round_windows(records)
        scope_keys = mine_review_rounds._round_scope(records, windows[0], {})
        expected_key = reviewer_yield._normalize_cited_path(cited_path, "/repo")
        assert expected_key in scope_keys
        assert scope_keys[expected_key] == cited_path

    def test_round_scope_collects_reviewer_subagent_reads(self, tmp_path):
        """A round's scope covers Reads by any reviewer-typed subagent it
        dispatched inside the window, not only the main thread's own
        Reads."""
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

        windows = review_rounds.detect_round_windows(records)
        dispatch_index, _meta_errors = corpus._index_subagent_dispatches(jsonl)
        scope_keys = mine_review_rounds._round_scope(records, windows[0], dispatch_index)
        expected_key = reviewer_yield._normalize_cited_path(cited_path, "/repo")
        assert expected_key in scope_keys


class TestMainThreadEditedBetween:
    def test_edit_of_a_line_suffixed_citation_target_is_detected(self):
        """A line-suffixed citation (":line", the shape TestMineReviewRoundsCandidates'
        own fixtures use) must still match a real edit of that same file, so the
        comparison normalizes the citation's raw_path before comparing it against the
        write target's clean path rather than comparing them as raw strings."""
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
    """mine() over synthetic transcripts, with the branch's git facts and
    resolve_defect_commits stubbed so these tests isolate the round-window /
    round-scope / citation-matching / grouping behavior from fixture-commit
    resolution (covered by TestResolveDefectCommits, and unstubbed by
    TestMineReviewRoundsAgainstRealGit)."""

    _CITED_PATH = "/repo/app.py"
    _DUMMY_RESOLUTION = mine_review_rounds._CommitResolution(
        ref_status="local-branch", base_commit=_SHA_A, head_commit=_SHA_B,
        fix_commit=_SHA_C, fix_date="2026-01-01T00:00:00+00:00",
    )
    _OTHER_FIX_RESOLUTION = mine_review_rounds._CommitResolution(
        ref_status="local-branch", base_commit=_SHA_A, head_commit=_SHA_B,
        fix_commit="d" * 40, fix_date="2026-01-02T00:00:00+00:00",
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

    def _build_one_later_round_matching_two_earlier_rounds_session(self, tmp_path) -> None:
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

    @staticmethod
    def _open_branch_git_touching_app_py(repo_dir, branch_ref):
        ref_status, ref = branch_ref
        if ref is None:
            return mine_review_rounds._BranchGit(ref_status=ref_status)
        return mine_review_rounds._BranchGit(
            ref_status=ref_status, ref=ref, merge_base=_SHA_A, touched_paths=frozenset({"app.py"}),
        )

    def _mine(self, tmp_path, monkeypatch, *, resolution_for_after_ts=None, branch_ref_resolver=None):
        monkeypatch.setattr(mine_review_rounds.scope, "_repo_scoped_project_slugs", lambda label: ["test-slug"])
        monkeypatch.setattr(mine_review_rounds, "resolve_pr_number", lambda *a, **k: None)
        monkeypatch.setattr(
            mine_review_rounds, "resolve_branch_ref",
            branch_ref_resolver or (lambda repo_dir, branch, pr_number: ("local-branch", f"refs/heads/{branch}")),
        )
        monkeypatch.setattr(mine_review_rounds, "open_branch_git", self._open_branch_git_touching_app_py)
        resolve = resolution_for_after_ts or (lambda after_ts: self._DUMMY_RESOLUTION)
        monkeypatch.setattr(
            mine_review_rounds, "resolve_defect_commits", lambda repo_dir, *, path, after_ts, branch: resolve(after_ts),
        )
        return mine_review_rounds.mine(tmp_path, roots=(tmp_path / "projects",))

    def test_later_round_citation_of_an_earlier_scoped_path_yields_a_candidate(self, tmp_path, monkeypatch):
        self._build_session(tmp_path)
        candidates = self._mine(tmp_path, monkeypatch)
        assert len(candidates) == 1
        assert candidates[0].evidence["path"] == "app.py"  # repo-relative, the only form git ever sees
        assert candidates[0].evidence["raw_citation"] == f"{self._CITED_PATH}:12"
        assert candidates[0].source == "review-round"

    def test_candidate_id_never_embeds_the_raw_branch_name(self, tmp_path, monkeypatch):
        """A branch name is this account's own text and can carry a private
        codename, unlike mine_szz.py's SZZ-sourced ids, which embed only
        already-public commit SHAs. id must carry an unsalted fingerprint
        of the branch instead of the branch itself."""
        branch = "acme-super-secret-project"
        self._build_session(tmp_path, branch=branch)
        candidates = self._mine(tmp_path, monkeypatch)
        assert len(candidates) == 1
        assert branch not in candidates[0].id
        fingerprint = mine_review_rounds._branch_fingerprint(branch)
        assert candidates[0].id.startswith(f"review-round:{fingerprint}:")

    def test_mine_run_twice_against_the_same_corpus_yields_the_same_candidate_id(self, tmp_path, monkeypatch):
        """confirm's re-run idempotency and defects.assert_unique_ids's
        collision guard both depend on _branch_fingerprint being stable
        across calls, not just within one mine() invocation.

        Both mine() calls run in this same process, so this only catches a
        per-call random-salt regression in _branch_fingerprint, not a
        per-process-cached salt that would look deterministic here but not
        across two separate CLI invocations."""
        self._build_session(tmp_path)
        first_run = self._mine(tmp_path, monkeypatch)
        second_run = self._mine(tmp_path, monkeypatch)
        assert first_run
        assert [candidate.id for candidate in first_run] == [candidate.id for candidate in second_run]

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

    def _build_two_later_rounds_citing_one_earlier_scoped_path_session(self, tmp_path) -> None:
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

    def test_two_later_rounds_resolving_to_different_fix_commits_get_distinct_candidates(self, tmp_path, monkeypatch):
        self._build_two_later_rounds_citing_one_earlier_scoped_path_session(tmp_path)
        second_later_round_ts = corpus._parse_ts("2026-01-03T00:00:00Z")

        candidates = self._mine(
            tmp_path, monkeypatch,
            resolution_for_after_ts=lambda after_ts: (
                self._OTHER_FIX_RESOLUTION if after_ts == second_later_round_ts else self._DUMMY_RESOLUTION
            ),
        )

        assert len(candidates) == 2
        assert len({c.id for c in candidates}) == 2
        assert {c.fix_commit for c in candidates} == {_SHA_C, "d" * 40}

    def test_two_later_rounds_sharing_a_fix_commit_yield_one_candidate_with_both_round_pairs(
        self, tmp_path, monkeypatch,
    ):
        self._build_two_later_rounds_citing_one_earlier_scoped_path_session(tmp_path)

        candidates = self._mine(tmp_path, monkeypatch)

        assert len(candidates) == 1
        assert [
            (pair["earlier_round_ts"], pair["later_round_ts"]) for pair in candidates[0].evidence["round_pairs"]
        ] == [
            (corpus._parse_ts("2026-01-01T00:00:00Z"), corpus._parse_ts("2026-01-02T00:00:00Z")),
            (corpus._parse_ts("2026-01-01T00:00:00Z"), corpus._parse_ts("2026-01-03T00:00:00Z")),
        ]

    def test_one_later_round_matching_two_earlier_rounds_yields_one_candidate_with_both_round_pairs(
        self, tmp_path, monkeypatch,
    ):
        """Two earlier rounds that each scoped the path, one later round citing
        it, one fix commit: a single defect, so a single candidate that keeps
        each round pair's own main_thread_edited_between evidence."""
        self._build_one_later_round_matching_two_earlier_rounds_session(tmp_path)

        candidates = self._mine(tmp_path, monkeypatch)

        assert len(candidates) == 1
        assert [
            (pair["earlier_round_ts"], pair["later_round_ts"], pair["main_thread_edited_between"])
            for pair in candidates[0].evidence["round_pairs"]
        ] == [
            (corpus._parse_ts("2026-01-01T00:00:00Z"), corpus._parse_ts("2026-01-03T00:00:00Z"), False),
            (corpus._parse_ts("2026-01-02T00:00:00Z"), corpus._parse_ts("2026-01-03T00:00:00Z"), False),
        ]

    def test_rounds_with_an_empty_branch_name_never_reach_gh_or_git(self, tmp_path, monkeypatch, capsys):
        self._build_session(tmp_path, branch="")

        def fail_if_called(*args, **kwargs):
            raise AssertionError("an empty branch name must not reach branch resolution")

        monkeypatch.setattr(mine_review_rounds, "_resolve_branch", fail_if_called)

        candidates = self._mine(tmp_path, monkeypatch)

        assert candidates == []
        assert "2 round(s) skipped for an empty branch name" in capsys.readouterr().err

    def test_resolve_branch_ref_is_called_once_per_branch_not_once_per_matching_pair(self, tmp_path, monkeypatch):
        """resolve_branch_ref can perform a real `git fetch`, so a later round
        matching two earlier rounds on the same branch must not repeat that
        lookup once per (later, earlier, key) match."""
        self._build_one_later_round_matching_two_earlier_rounds_session(tmp_path)

        calls: list[str] = []

        def counting_resolve_branch_ref(repo_dir, branch, pr_number):
            calls.append(branch)
            return "local-branch", f"refs/heads/{branch}"

        candidates = self._mine(tmp_path, monkeypatch, branch_ref_resolver=counting_resolve_branch_ref)

        assert len(candidates) == 1  # two matching (later, earlier, key) pairs sharing one fix commit
        assert calls == ["feat"]  # resolve_branch_ref called once, not once per pair

    def test_a_fetch_failed_branch_is_not_re_fetched_per_matching_pair(self, tmp_path, monkeypatch):
        """A fetch-failed branch resolves once and every match on it is
        counted as unresolved, rather than re-fetching once per matching
        (later, earlier, key) pair."""
        self._build_one_later_round_matching_two_earlier_rounds_session(tmp_path)

        calls: list[str] = []

        def failing_resolve_branch_ref(repo_dir, branch, pr_number):
            calls.append(branch)
            return "fetch-failed", None

        candidates = self._mine(tmp_path, monkeypatch, branch_ref_resolver=failing_resolve_branch_ref)

        assert candidates == []
        assert calls == ["feat"]  # resolve_branch_ref called once despite two matching pairs


class TestMineReviewRoundsAgainstRealGit:
    """mine() with only the session-slug lookup stubbed: a real tmp git repo
    with dated commits, and synthetic transcripts whose citations are
    absolute worktree paths with a line suffix, the shape reviewers emit."""

    _WORKTREE = "/work/repo/.claude/worktrees/GH-1114/my-slug"

    def _build(self, tmp_path, monkeypatch, *, relative_path: str, cited_relative_path: str | None = None) -> dict:
        cited_relative_path = cited_relative_path or relative_path
        repo = _init_repo(tmp_path / "repo")
        _write(repo, relative_path, "line0\n")
        _write(repo, "pkg/untouched.py", "untouched = 1\n")
        seed_sha = _commit_at(repo, "seed", "2026-01-01T00:00:00+0000")
        _set_origin_main(repo, seed_sha)
        _git(repo, "checkout", "-q", "-b", "feat")
        _write(repo, relative_path, "line0\nbad_value\n")
        introducing_sha = _commit_at(repo, "introduce bad value", "2026-01-01T06:00:00+0000")
        # Rounds 1 and 2 (Jan 2, Jan 3) read the file; round 3 (Jan 4) cites
        # it. Both commits below post-date round 3.
        _write(repo, relative_path, "line0\ngood_value\n")
        first_fix_sha = _commit_at(repo, "respond to round three", "2026-01-04T12:00:00+0000")
        _write(repo, relative_path, "line0\nbetter_value\n")
        _commit_at(repo, "rework the value again", "2026-01-05T12:00:00+0000")

        proj = tmp_path / "projects" / "test-slug"
        proj.mkdir(parents=True)
        cited_path = f"{self._WORKTREE}/{cited_relative_path}"
        dispatch_id = "toolu_agent1"
        records = [
            _assistant(ts="2026-01-02T00:00:00Z", cwd=self._WORKTREE, content=[_skill_use("s1", "code-review")]),
            _assistant(ts="2026-01-02T00:01:00Z", cwd=self._WORKTREE, content=[_read_use("r1", cited_path)]),
            _user("looks fine", ts="2026-01-02T00:02:00Z"),
            _assistant(ts="2026-01-03T00:00:00Z", cwd=self._WORKTREE, content=[_skill_use("s2", "code-review")]),
            _assistant(ts="2026-01-03T00:01:00Z", cwd=self._WORKTREE, content=[_read_use("r2", cited_path)]),
            _user("still fine", ts="2026-01-03T00:02:00Z"),
            _assistant(ts="2026-01-04T00:00:00Z", cwd=self._WORKTREE, content=[_skill_use("s3", "code-review")]),
            _assistant(
                ts="2026-01-04T00:01:00Z", cwd=self._WORKTREE,
                content=[_agent_use(dispatch_id, "staff-backend-engineer")],
            ),
            _user("thanks", ts="2026-01-04T00:02:00Z"),
        ]
        _write_jsonl(proj / "sess-1.jsonl", records)
        finding_text = f"Reviewer finding: {cited_path}:12 still holds bad_value; see also {cited_relative_path} for context"
        _write_subagent_dispatch(
            proj, "sess-1", "agent-1", dispatch_id,
            [_assistant(ts="2026-01-04T00:01:30Z", cwd=self._WORKTREE, content=[{"type": "text", "text": finding_text}])],
        )
        monkeypatch.setattr(mine_review_rounds.scope, "_repo_scoped_project_slugs", lambda label: ["test-slug"])
        return {
            "repo": repo, "roots": (tmp_path / "projects",), "seed_sha": seed_sha,
            "introducing_sha": introducing_sha, "first_fix_sha": first_fix_sha, "cited_path": cited_path,
        }

    @pytest.mark.parametrize(
        ("relative_path", "expected_lens", "expected_markdown"),
        [
            ("pkg/app.py", "staff-backend-engineer", False),
            ("docs/guide.md", "comment-discipline-reviewer", True),
        ],
    )
    def test_worktree_path_citation_across_two_earlier_rounds_yields_one_candidate_at_the_earliest_fix(
        self, tmp_path, monkeypatch, relative_path, expected_lens, expected_markdown,
    ):
        built = self._build(tmp_path, monkeypatch, relative_path=relative_path)

        candidates = mine_review_rounds.mine(built["repo"], roots=built["roots"])

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.fix_commit == built["first_fix_sha"]  # not the later rework commit
        assert candidate.head_commit == built["introducing_sha"]
        assert candidate.base_commit == built["seed_sha"]
        assert candidate.fix_date == "2026-01-04T12:00:00+00:00"
        assert candidate.ref_status == "local-branch"
        assert candidate.lens == expected_lens
        assert candidate.file_is_markdown is expected_markdown
        assert candidate.id == (
            f"review-round:{mine_review_rounds._branch_fingerprint('feat')}:"
            f"{mine_review_rounds._path_fingerprint(relative_path)}:{built['first_fix_sha'][:12]}"
        )
        assert candidate.evidence["path"] == relative_path
        assert candidate.evidence["raw_citation"] == f"{built['cited_path']}:12"
        assert [
            (pair["earlier_round_ts"], pair["later_round_ts"]) for pair in candidate.evidence["round_pairs"]
        ] == [
            (_ts("2026-01-02T00:00:00Z"), _ts("2026-01-04T00:00:00Z")),
            (_ts("2026-01-03T00:00:00Z"), _ts("2026-01-04T00:00:00Z")),
        ]

    def test_citation_of_a_path_the_branch_never_touched_yields_no_candidate(self, tmp_path, monkeypatch, capsys):
        built = self._build(
            tmp_path, monkeypatch, relative_path="pkg/app.py", cited_relative_path="pkg/untouched.py",
        )

        assert mine_review_rounds.mine(built["repo"], roots=built["roots"]) == []
        assert "2 citation(s) skipped for unresolved commits" in capsys.readouterr().err  # one per earlier round


# --- confirm CLI --------------------------------------------------------------


def _confirm_args(local_dir: Path, defects_path: Path):
    return run_review_bench.build_parser().parse_args(
        ["confirm", "--local-dir", str(local_dir), "--defects-path", str(defects_path)]
    )


def _feed_answers(monkeypatch, *answers: str, terminal: bool = True) -> None:
    """Makes `confirm` see `terminal` and read one line per answer from stdin. Once the answers run
    out, stdin is at end of input. Every `confirm` test goes through this helper, so none reaches the
    real stdin, which a `pytest -s` run can leave attached to a terminal."""
    monkeypatch.setattr(run_review_bench, "_stdin_is_terminal", lambda: terminal)
    monkeypatch.setattr(sys, "stdin", io.StringIO("".join(f"{answer}\n" for answer in answers)))


def _pin_refs(repo: Path) -> list[str]:
    return _git(repo, "for-each-ref", "--format=%(refname)", "refs/review-bench/").split()


class TestStdinIsTerminal:
    """The real `_stdin_is_terminal()`, with nothing patched around it."""

    def test_a_string_io_stdin_is_not_a_terminal(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))
        assert run_review_bench._stdin_is_terminal() is False

    def test_no_stdin_is_not_a_terminal(self, monkeypatch):
        monkeypatch.setattr(sys, "stdin", None)
        assert run_review_bench._stdin_is_terminal() is False

    def test_a_closed_stdin_is_not_a_terminal(self, monkeypatch):
        closed_stdin = io.StringIO("")
        closed_stdin.close()
        monkeypatch.setattr(sys, "stdin", closed_stdin)
        assert run_review_bench._stdin_is_terminal() is False

    def test_an_object_whose_isatty_is_true_is_a_terminal(self, monkeypatch):
        class TerminalStandIn:
            def isatty(self):
                return True

        monkeypatch.setattr(sys, "stdin", TerminalStandIn())
        assert run_review_bench._stdin_is_terminal() is True

    def test_no_command_line_flag_reaches_the_seam(self, capsys):
        parser = run_review_bench.build_parser()
        assert set(vars(parser.parse_args(["confirm"]))) == {"subcommand", "local_dir", "defects_path", "func"}
        for flag in ("--yes", "--tty", "--terminal", "--assume-yes", "--non-interactive"):
            with pytest.raises(SystemExit) as exit_info:
                parser.parse_args(["confirm", flag])
            assert exit_info.value.code == 2


class TestConfirmCli:
    def _repo_with_two_commits(self, tmp_path) -> tuple[Path, str, str]:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        return repo, introducing_sha, fix_sha

    def _repo_with_a_passing_candidate(self, tmp_path, monkeypatch, **overrides) -> tuple[Path, Path, Path, str]:
        """Returns (repo, local_dir, defects_path, fix_sha) for one candidate, `c-1`, that passes every check."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        kwargs = dict(
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        )
        defects.save_candidates(
            local_dir / "szz_candidates.json", [Candidate(**_candidate_kwargs(**{**kwargs, **overrides}))],
        )
        return repo, local_dir, tmp_path / "defects.json", fix_sha

    def test_rejection_prints_only_id_six_gram_and_source_candidate_id(self, tmp_path, monkeypatch, capsys):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        _feed_answers(monkeypatch)

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

    def test_the_prompt_shows_the_candidates_identity_guesses_path_description_and_commit_subjects(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The prompt is the engineer's one look at a candidate before it lands in the committed
        defects.json. The four inclusion fields print last, beside the prompt."""
        repo, local_dir, defects_path, fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, evidence={"path": "src/app.py"}, lines_exist_at_introducing_head=False,
        )
        introducing_sha = _git(repo, "rev-parse", f"{fix_sha}^").strip()
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        for shown in (
            "candidate c-1", "source: szz", "fix_date: 2026-01-01T00:00:00+00:00", f"introducing {introducing_sha[:12]}",
            f"fix {fix_sha[:12]}", "path: src/app.py", f"{fix_sha[:12]} fix x value", f"{introducing_sha[:12]} introduce x",
            "description: x was left at its stale initial value.", "lens: staff-backend-engineer",
            "reviewer_could_have_caught_it: True (unchecked default)",
            "lines_exist_at_introducing_head: False (unchecked default)", "file_is_markdown: False",
        ):
            assert shown in stderr
        assert fix_sha not in stderr  # 12-character SHAs only
        assert stderr.rindex("description:") < stderr.rindex("inclusion fields:") < stderr.rindex("[y/N/q]")

    def test_the_prompt_bounds_the_subject_count_and_each_subjects_length(self, tmp_path, monkeypatch, capsys):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "other.py", "y = 1\n")
        base_sha = _commit(repo, "add other module")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        monkeypatch.setattr(run_review_bench, "_CONFIRM_MAX_COMMIT_SUBJECTS", 1)
        monkeypatch.setattr(run_review_bench, "_CONFIRM_MAX_SUBJECT_CHARS", 5)
        local_dir = tmp_path / "local"
        defects.save_candidates(local_dir / "szz_candidates.json", [Candidate(**_candidate_kwargs(
            id="c-1", base_commit=base_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        ))])
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        stderr = capsys.readouterr().err
        assert f"{introducing_sha[:12]} intro\n" in stderr
        assert "introduce x" not in stderr  # cut to 5 characters
        assert "fix x" not in stderr and "add other" not in stderr  # past the count bound

    def test_confirm_appends_a_candidate_answered_y_and_is_idempotent_on_rerun(self, tmp_path, monkeypatch):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        args = _confirm_args(local_dir, defects_path)

        _feed_answers(monkeypatch, "y")
        assert run_review_bench.cmd_confirm(args) == 0
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]

        _feed_answers(monkeypatch)  # a retry finds c-1 already confirmed, so it prompts for nothing
        assert run_review_bench.cmd_confirm(args) == 0
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]

    def test_confirm_pins_an_approved_defects_fix_commit_to_a_local_ref(self, tmp_path, monkeypatch):
        repo, local_dir, defects_path, fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, id="review-round:abc:def:123",
        )
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        pin_ref = defects.defect_pin_ref("review-round:abc:def:123")
        assert _git(repo, "rev-parse", pin_ref).strip() == fix_sha
        assert _pin_refs(repo) == [pin_ref]

    def test_confirm_rejects_an_approved_candidate_whose_fix_commit_cannot_be_pinned(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "y")

        def failing_pin(repo_dir, defect):
            raise subprocess.CalledProcessError(1, ["git", "update-ref"], stderr="cannot lock ref")

        monkeypatch.setattr(defects, "pin_defect_commits", failing_pin)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert defects.load_confirmed_defects(defects_path) == []
        assert "could not pin its fix commit" in capsys.readouterr().err

    @pytest.mark.parametrize(
        "defect_id", ["szz:0123456789ab:cdef01234567:pkg/a~b.py", "review-round:abc:def:123", "c-1"],
    )
    def test_defect_pin_ref_is_a_valid_ref_name_for_any_defect_id(self, defect_id):
        pin_ref = defects.defect_pin_ref(defect_id)

        assert pin_ref.startswith("refs/review-bench/")
        subprocess.run(["git", "check-ref-format", pin_ref], check=True)

    def test_a_candidate_with_an_empty_description_is_skipped_without_a_prompt(self, tmp_path, monkeypatch, capsys):
        """An empty description is a completeness check, not approval: the candidate is neither
        prompted for nor counted as awaiting the engineer."""
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description="",
        )
        _feed_answers(monkeypatch, "y")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert "[y/N/q]" not in capsys.readouterr().err
        assert defects.load_confirmed_defects(defects_path) == []
        assert sys.stdin.read() == "y\n"  # the answer was never consumed

    def test_confirm_skips_one_unresolvable_candidate_but_processes_the_rest(self, tmp_path, monkeypatch):
        """One candidate whose commits are unreachable (rewritten history, a stale
        .local/ shortlist) must not crash confirmation for every other candidate
        in the same run, since public_git_text's `git show` (check=True) can raise
        at its cmd_confirm call site."""
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
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-good"]

    def test_confirm_aborts_without_overwriting_when_defects_file_changed_concurrently(self, tmp_path, monkeypatch):
        """cmd_confirm's read-modify-write of the committed defects.json must abort
        rather than overwrite when an overlapping confirm run's own append has
        already changed the file underneath its stale read."""
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
        _feed_answers(monkeypatch, "y")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 1
        ids = {d.id for d in original_load(defects_path)}
        assert ids == {"concurrent"}  # this run's own append must not overwrite it

    def test_confirm_aborts_when_the_defects_file_was_swapped_for_a_set_of_the_same_size(self, tmp_path, monkeypatch):
        """An add paired with a remove leaves the defect count unchanged, so the staleness guard
        compares the defects themselves."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        defects.save_candidates(local_dir / "szz_candidates.json", [Candidate(**_candidate_kwargs(
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.",
        ))])
        defects_path = tmp_path / "defects.json"

        def confirmed(defect_id: str) -> ConfirmedDefect:
            return ConfirmedDefect(
                id=defect_id, source="szz", lens="staff-backend-engineer", base_commit=_SHA_A, head_commit=_SHA_B,
                fix_commit=_SHA_C, fix_date="2026-01-01T00:00:00+00:00", description=f"{defect_id} description.",
            )

        defects.save_confirmed_defects(defects_path, [confirmed("loaded")])
        original_load = defects.load_confirmed_defects
        swapped = {"done": False}

        def load_then_swap_once(path):
            result = original_load(path)
            if not swapped["done"]:
                swapped["done"] = True
                defects.save_confirmed_defects(path, [confirmed("swapped-in")])
            return result

        monkeypatch.setattr(defects, "load_confirmed_defects", load_then_swap_once)
        _feed_answers(monkeypatch, "y")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 1
        assert [d.id for d in original_load(defects_path)] == ["swapped-in"]

    # --- the [y/N/q] prompt -------------------------------------------------

    def _two_passing_candidates(self, tmp_path, monkeypatch, count: int = 2) -> tuple[Path, Path, Path, str]:
        """Returns (repo, local_dir, defects_path, fix_sha) for candidates c-1 .. c-<count>, each of which passes every check."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        defects.save_candidates(local_dir / "szz_candidates.json", [
            Candidate(**_candidate_kwargs(
                id=f"c-{index}", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
                description=f"x {index} was left at its stale initial value.",
            ))
            for index in range(1, count + 1)
        ])
        return repo, local_dir, tmp_path / "defects.json", fix_sha

    @pytest.mark.parametrize("answer", ["", "n", "N", "Y", " y ", "yes", "y ", "ok", "1"])
    def test_any_answer_but_exactly_y_or_q_skips_the_candidate_and_leaves_it_in_local(
        self, tmp_path, monkeypatch, answer,
    ):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, answer)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert defects.load_confirmed_defects(defects_path) == []
        assert not defects_path.exists()
        assert _pin_refs(repo) == []
        assert [c.id for c in defects.load_candidates(local_dir / "szz_candidates.json")] == ["c-1"]

    def test_y_promotes_and_a_later_n_skips(self, tmp_path, monkeypatch, capsys):
        repo, local_dir, defects_path, fix_sha = self._two_passing_candidates(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "y", "n")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]
        assert "1 appended, 1 skipped, 0 rejected" in capsys.readouterr().err

    def test_q_stops_the_loop_writes_the_candidates_accepted_so_far_and_never_prompts_for_the_rest(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, local_dir, defects_path, _fix_sha = self._two_passing_candidates(tmp_path, monkeypatch, count=3)
        _feed_answers(monkeypatch, "y", "q", "y")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]
        assert capsys.readouterr().err.count("[y/N/q]") == 2
        assert sys.stdin.read() == "y\n"  # c-3's answer was never consumed

    def test_end_of_input_stops_the_loop_and_writes_the_candidates_accepted_so_far(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, local_dir, defects_path, _fix_sha = self._two_passing_candidates(tmp_path, monkeypatch, count=3)
        _feed_answers(monkeypatch, "y")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]
        assert capsys.readouterr().err.count("[y/N/q]") == 2  # c-2 hit end of input, so c-3 was never shown

    def test_end_of_input_at_the_first_prompt_writes_and_pins_nothing(self, tmp_path, monkeypatch):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        _feed_answers(monkeypatch)

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 0

        assert not defects_path.exists()
        assert _pin_refs(repo) == []

    def test_ctrl_c_writes_and_pins_nothing_even_after_an_accepted_candidate(self, tmp_path, monkeypatch, capsys):
        repo, local_dir, defects_path, _fix_sha = self._two_passing_candidates(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "y")

        class InterruptedAfterFirstAnswer:
            def __init__(self, first_answer: io.StringIO):
                self.first_answer = first_answer

            def readline(self):
                line = self.first_answer.readline()
                if not line:
                    raise KeyboardInterrupt
                return line

        monkeypatch.setattr(sys, "stdin", InterruptedAfterFirstAnswer(sys.stdin))

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 130
        assert not defects_path.exists()
        assert _pin_refs(repo) == []
        assert "interrupted" in capsys.readouterr().err

    def test_sigterm_at_the_prompt_returns_128_plus_sigterm_and_writes_nothing(self, tmp_path, monkeypatch, capsys):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        _feed_answers(monkeypatch)

        def terminated_at_the_prompt() -> str:
            raise run_review_bench._SignalInterrupt(signal.SIGTERM)

        monkeypatch.setattr(run_review_bench, "_read_promotion_decision", terminated_at_the_prompt)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 128 + signal.SIGTERM
        assert not defects_path.exists()
        assert _pin_refs(repo) == []
        assert "interrupted" in capsys.readouterr().err

    def test_no_pin_ref_exists_while_the_prompt_is_showing_and_one_exists_after_an_accepted_answer(
        self, tmp_path, monkeypatch,
    ):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        refs_seen_at_prompt_time: list[list[str]] = []

        class AnswerYAfterCheckingRefs:
            def readline(self):
                refs_seen_at_prompt_time.append(_pin_refs(repo))
                return "y\n"

        monkeypatch.setattr(run_review_bench, "_stdin_is_terminal", lambda: True)
        monkeypatch.setattr(sys, "stdin", AnswerYAfterCheckingRefs())

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 0

        assert refs_seen_at_prompt_time == [[]]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]

    def test_a_skipped_candidate_is_never_pinned(self, tmp_path, monkeypatch):
        repo, local_dir, defects_path, _fix_sha = self._two_passing_candidates(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "n", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert _pin_refs(repo) == [defects.defect_pin_ref("c-2")]

    # --- no terminal --------------------------------------------------------

    def test_a_string_io_stdin_with_the_seam_unpatched_writes_nothing_and_pins_nothing(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The deny half of a pair with the next test: the same stdin content, and the same "y",
        promotes only once the seam reports a terminal."""
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 2
        assert not defects_path.exists()
        assert _pin_refs(repo) == []
        assert "1 candidate(s) awaiting the engineer at a terminal" in capsys.readouterr().err

    def test_the_same_stdin_content_promotes_once_the_seam_reports_a_terminal(self, tmp_path, monkeypatch):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        monkeypatch.setattr(run_review_bench, "_stdin_is_terminal", lambda: True)
        monkeypatch.setattr(sys, "stdin", io.StringIO("y\n"))

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 0

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]

    def test_without_a_terminal_confirm_runs_every_check_prints_rejections_and_counts_the_candidates_awaiting(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        defects.save_candidates(local_dir / "szz_candidates.json", [
            Candidate(**_candidate_kwargs(id="src-1", excerpt="the reviewer found that error handling swallows it", **commits)),
            Candidate(**_candidate_kwargs(
                id="c-copied", description="The reviewer found that error handling swallows it.", **commits,
            )),
            Candidate(**_candidate_kwargs(id="c-ok-1", description="x 1 was left at its stale initial value.", **commits)),
            Candidate(**_candidate_kwargs(id="c-ok-2", description="x 2 was left at its stale initial value.", **commits)),
            Candidate(**_candidate_kwargs(id="c-blank", description="", **commits)),
        ])
        defects_path = tmp_path / "defects.json"
        _feed_answers(monkeypatch, "y", "y", terminal=False)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert exit_code == 2
        assert "rejected c-copied" in stderr  # the checks ran
        assert "2 candidate(s) awaiting the engineer at a terminal, 1 rejected" in stderr
        assert "[y/N/q]" not in stderr
        assert not defects_path.exists()
        assert _pin_refs(repo) == []

    @pytest.mark.parametrize(
        "descriptions", [[], [""], ["The reviewer found that error handling swallows it."]],
        ids=["no-candidates", "only-empty-descriptions", "only-a-rejected-candidate"],
    )
    def test_without_a_terminal_confirm_exits_0_when_no_candidate_awaits(
        self, tmp_path, monkeypatch, capsys, descriptions,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        candidates = [
            Candidate(**_candidate_kwargs(
                id="src-1", excerpt="the reviewer found that error handling swallows it", **commits,
            )),
            *[
                Candidate(**_candidate_kwargs(id=f"c-{index}", description=description, **commits))
                for index, description in enumerate(descriptions)
            ],
        ]
        defects.save_candidates(local_dir / "szz_candidates.json", candidates)
        _feed_answers(monkeypatch, terminal=False)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        assert exit_code == 0
        assert "0 candidate(s) awaiting" in capsys.readouterr().err

    # --- terminal-unsafe text -----------------------------------------------

    _HOSTILE = "\x1b[31m\r\N{RIGHT-TO-LEFT OVERRIDE}"

    def _assert_hostile_text_only_escaped(self, stderr: str) -> None:
        for raw in ("\x1b", "\r", "\N{RIGHT-TO-LEFT OVERRIDE}"):
            assert raw not in stderr
        for escaped in ("\\x1b", "\\r", "\\u202e"):
            assert escaped in stderr

    def test_escape_carriage_return_and_bidi_override_in_the_id_subject_description_and_path_show_escaped(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        introducing_sha = _commit(repo, f"introduce x{self._HOSTILE}")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        defects.save_candidates(local_dir / "szz_candidates.json", [Candidate(**_candidate_kwargs(
            id=f"c-1{self._HOSTILE}", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description=f"x was left at its stale initial value.{self._HOSTILE}",
            evidence={"path": f"app{self._HOSTILE}.py"},
        ))])
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "introduce x\\x1b[31m" in stderr  # the subject, not only the id, reached the display

    def test_hostile_text_in_a_rejected_candidates_id_and_word_run_is_escaped_in_the_rejection_line(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        defects.save_candidates(local_dir / "szz_candidates.json", [
            Candidate(**_candidate_kwargs(
                id=f"src-1{self._HOSTILE}", excerpt="the reviewer found that error handling swallows it", **commits,
            )),
            Candidate(**_candidate_kwargs(
                id=f"c-2{self._HOSTILE}", description="The reviewer found that error handling swallows it.", **commits,
            )),
        ])
        _feed_answers(monkeypatch)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)

    @pytest.mark.parametrize(
        "field", ["lens", "source", "fix_date", "lines_exist_at_introducing_head", "reviewer_could_have_caught_it",
                  "file_is_markdown"],
    )
    def test_hostile_text_in_any_field_of_the_approval_display_shows_escaped(self, field, capsys):
        candidate = Candidate(**_candidate_kwargs())
        # Sets the value past the validation `lens`, `source`, and `fix_date` get at construction:
        # the display escapes every field it prints, whatever validation stands upstream.
        object.__setattr__(candidate, field, self._HOSTILE)

        run_review_bench._print_candidate_for_approval(candidate, [])

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)

    @pytest.mark.parametrize(
        "field", ["lines_exist_at_introducing_head", "reviewer_could_have_caught_it", "file_is_markdown"],
    )
    def test_a_string_in_a_shortlisted_inclusion_flag_reaches_the_prompt_escaped(
        self, field, tmp_path, monkeypatch, capsys,
    ):
        """The flags are not type-checked on load, so a hand-edited or model-derived `.local/`
        value reaches the terminal beside the prompt."""
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, **{field: self._HOSTILE},
        )
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)

    def test_hostile_text_in_the_git_error_rejection_line_is_escaped(self, monkeypatch, capsys):
        def unreadable_git_text(*args):
            raise subprocess.CalledProcessError(128, self._HOSTILE)  # a str cmd prints raw in str(exc)

        monkeypatch.setattr(defects, "public_git_text", unreadable_git_text)
        candidate = Candidate(**_candidate_kwargs(id=f"c-1{self._HOSTILE}"))

        assert run_review_bench._defect_passing_confirm_checks(candidate, {}) is None

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)

    def test_hostile_text_in_the_schema_rejection_line_is_escaped(self, monkeypatch, capsys):
        def rejecting_confirmed_defect(**_kwargs):
            raise ValueError(self._HOSTILE)

        monkeypatch.setattr(defects, "public_git_text", lambda *args: "")
        monkeypatch.setattr(defects, "ConfirmedDefect", rejecting_confirmed_defect)
        candidate = Candidate(**_candidate_kwargs(id=f"c-1{self._HOSTILE}", description="x is stale."))

        assert run_review_bench._defect_passing_confirm_checks(candidate, {}) is None

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)
