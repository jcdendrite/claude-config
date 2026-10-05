"""Tests for evals/run_review_bench.py's own CLI-level logic across `freeze`,
`run`, `smoke`, `judge`, `analyze`, `spot-check`, `mine-szz`, `mine-rounds`,
`mine-pr-comments`, and `snapshot-arms`: argument handling, frozen-condition checks, report
contents, and exit codes, plus `analysis.hash_directory`, `cmd_freeze`'s
written manifest fields, and `_build_spot_check_samples`'s reviewer/judge
join. Offline throughout -- `cmd_freeze`'s own git calls run against a
throwaway tmp-path repo, never this repo's own history. No test launches
`claude`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import signal
import stat
import subprocess
from pathlib import Path

import pytest
import run_review_bench
from review_bench import analysis, defects, runner
from review_bench import arms as arms_mod
from review_bench.adjudicate import JUDGE_ARM_PRECISION, JUDGE_ARM_RECALL
from review_bench.defects import ConfirmedDefect
from test_review_bench_mining import _commit, _init_repo, _write

_ARM_1, _ARM_2 = arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT


def _run_record(
    defect_id: str, arm: str, opaque_run_id: str, findings_text: str, *, status: str = "ok",
    out_of_session_paths: tuple[str, ...] = (), over_read_cap: bool = False, run_index: int = 0,
) -> runner.RunRecord:
    return runner.RunRecord(
        campaign_id="c1", defect_id=defect_id, arm=arm, run_index=run_index, opaque_run_id=opaque_run_id,
        status=status, missing_reason=None, observed_model="claude-sonnet-5", observed_tools=("Read",),
        out_of_session_paths=out_of_session_paths, findings_text=findings_text, wall_clock_s=1.0, read_calls=1,
        read_tokens_est=10, partial_view_reads=0, paged_followups=0, whole_file_reads_of_changed_files=0,
        over_read_cap=over_read_cap, dispatch_prompt_verbatim=True, cli_version="2.0.0",
        ambient_config_commit="deadbeef",
    )


class TestHashDirectory:
    def test_deterministic_for_the_same_contents(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("hello")
        (directory / "b.txt").write_text("world")
        assert analysis.hash_directory(directory) == analysis.hash_directory(directory)

    def test_sensitive_to_a_content_change(self, tmp_path: Path) -> None:
        directory = tmp_path / "dir"
        directory.mkdir()
        (directory / "a.txt").write_text("hello")
        (directory / "b.txt").write_text("world")
        before = analysis.hash_directory(directory)
        (directory / "b.txt").write_text("world!")
        after = analysis.hash_directory(directory)
        assert before != after


# Every field evals/README.md's "Frozen conditions and invalidation" section
# requires cmd_freeze to record in conditions.json.
_EXPECTED_FREEZE_CONDITIONS_FIELDS: frozenset[str] = frozenset({
    "reviewer_model_id", "judge_model_id", "k", "delta", "alpha_one_sided", "n_min",
    "planning_variance", "bootstrap_resamples", "bootstrap_seed", "campaign_seed",
    "kappa_floor", "missing_run_retry_rule", "later_arm_gate", "defect_ids",
    "harness_closure_hash", "harness_closure", "arm_dir_hashes", "judge_agent_hashes",
    "defects_json_hash", "prompt_template_hashes", "environment", "main_commit_sha", "gating_rule", "gate_counts",
})


_STUBBED_ENVIRONMENT = runner.EnvironmentRecord("2.0.0", "deadbeef")


@pytest.fixture
def stubbed_environment(monkeypatch: pytest.MonkeyPatch) -> runner.EnvironmentRecord:
    """Keeps every test that reaches `runner.read_environment_record` off the
    real `claude` binary and the ambient checkout."""
    monkeypatch.setattr(runner, "read_environment_record", lambda **_kwargs: _STUBBED_ENVIRONMENT)
    return _STUBBED_ENVIRONMENT


@pytest.mark.usefixtures("stubbed_environment")
class TestCmdFreezeManifestFields:
    def test_writes_every_readme_required_field_including_arm_dir_judge_agent_defects_json_and_prompt_template_hashes(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        head_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        defects_path = tmp_path / "defects.json"
        confirmed = [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit=head_sha, head_commit=head_sha,
                fix_commit=fix_sha, fix_date="2024-01-01", description="x changed its stale initial value.",
                path="app.py", file_is_markdown=False,
            ),
        ]
        defects.save_confirmed_defects(defects_path, confirmed)

        # An empty candidates shortlist is enough to satisfy the
        # local-excerpts-present precondition without contributing any
        # excerpt text for the provenance check to compare against.
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "szz_candidates.json").write_text("[]")

        arms_root = tmp_path / "arms"
        for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
            arm_dir = arms_root / arm
            arm_dir.mkdir(parents=True)
            (arm_dir / "bench-staff-backend-engineer.md").write_text(f"{arm} agent body\n")

        manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
        out_path = tmp_path / "conditions.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), local_dir=str(local_dir), arms_root=str(arms_root), k=10,
            campaign_seed=7, last_smoke_manifest_hash=manifest_hash, smoke_full_k=10, main_commit_sha="",
            out=str(out_path),
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 0
        conditions = json.loads(out_path.read_text())

        # Completeness, not just the hand-picked subset asserted below: a
        # field evals/README.md requires but cmd_freeze drops, or an extra
        # field it adds, fails here even if that field's value is never checked.
        assert set(conditions.keys()) == _EXPECTED_FREEZE_CONDITIONS_FIELDS
        assert conditions["campaign_seed"] == 7

        from review_bench import adjudicate

        expected_arm_dir_hashes = {
            arm: analysis.hash_directory(arms_root / arm)
            for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT)
        }
        assert conditions["arm_dir_hashes"] == expected_arm_dir_hashes
        # The two arms' own snapshot files differ in content (different arm
        # names above), so a copy-paste bug hashing one arm's directory for
        # both would collide here.
        assert expected_arm_dir_hashes[arms_mod.ARM_CURRENT_RULE] != expected_arm_dir_hashes[arms_mod.ARM_FUNCTION_CONTEXT]

        assert conditions["judge_agent_hashes"] == {
            "bench-judge-recall": hashlib.sha256(adjudicate.RECALL_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
            "bench-judge-precision": hashlib.sha256(adjudicate.PRECISION_JUDGE_AGENT_FILE.read_bytes()).hexdigest(),
        }
        assert conditions["defects_json_hash"] == hashlib.sha256(defects_path.read_bytes()).hexdigest()
        assert conditions["prompt_template_hashes"] == {
            "review_prompt": hashlib.sha256(runner.REVIEW_PROMPT_TEMPLATE.encode()).hexdigest(),
            "dispatch_prompt": hashlib.sha256(runner.DISPATCH_PROMPT_TEMPLATE.encode()).hexdigest(),
            "judge_inner_prompt": hashlib.sha256(adjudicate.JUDGE_INNER_PROMPT_TEMPLATE.encode()).hexdigest(),
        }

    def test_cli_flags_reach_cmd_freeze_via_build_parser(self, tmp_path: Path, monkeypatch) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        head_sha = _commit(repo, "introduce x")
        _write(repo, "app.py", "x = 2\n")
        fix_sha = _commit(repo, "fix x value")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

        defects_path = tmp_path / "defects.json"
        confirmed = [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit=head_sha, head_commit=head_sha,
                fix_commit=fix_sha, fix_date="2024-01-01", description="x changed its stale initial value.",
                path="app.py", file_is_markdown=False,
            ),
        ]
        defects.save_confirmed_defects(defects_path, confirmed)

        local_dir = tmp_path / "local"
        local_dir.mkdir()
        (local_dir / "szz_candidates.json").write_text("[]")

        arms_root = tmp_path / "arms"
        for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
            arm_dir = arms_root / arm
            arm_dir.mkdir(parents=True)
            (arm_dir / "bench-staff-backend-engineer.md").write_text(f"{arm} agent body\n")

        manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
        out_path = tmp_path / "conditions.json"
        args = run_review_bench.build_parser().parse_args([
            "freeze", "--defects-path", str(defects_path), "--local-dir", str(local_dir),
            "--arms-root", str(arms_root), "--k", "10", "--campaign-seed", "7",
            "--last-smoke-manifest-hash", manifest_hash, "--smoke-full-k", "10", "--out", str(out_path),
        ])

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 0
        conditions = json.loads(out_path.read_text())
        assert conditions["campaign_seed"] == 7


def _prepare_freeze(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> argparse.Namespace:
    """A throwaway repo, one confirmed defect, both arm dirs, and the args a
    passing `freeze` needs."""
    repo = _init_repo(tmp_path / "repo")
    _write(repo, "app.py", "x = 1\n")
    head_sha = _commit(repo, "introduce x")
    _write(repo, "app.py", "x = 2\n")
    fix_sha = _commit(repo, "fix x value")
    monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)

    defects_path = tmp_path / "defects.json"
    defects.save_confirmed_defects(defects_path, [
        ConfirmedDefect(
            id="d1", source="szz", lens="staff-backend-engineer", base_commit=head_sha, head_commit=head_sha,
            fix_commit=fix_sha, fix_date="2024-01-01", description="x changed its stale initial value.",
            path="app.py", file_is_markdown=False,
        ),
    ])
    local_dir = tmp_path / "local"
    local_dir.mkdir()
    (local_dir / "szz_candidates.json").write_text("[]")
    arms_root = tmp_path / "arms"
    for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
        (arms_root / arm).mkdir(parents=True)
        (arms_root / arm / "bench-staff-backend-engineer.md").write_text(f"{arm} agent body\n")

    manifest_hash = analysis.closure_manifest_hash(analysis.compute_harness_closure())
    return argparse.Namespace(
        defects_path=str(defects_path), local_dir=str(local_dir), arms_root=str(arms_root), k=10, campaign_seed=7,
        last_smoke_manifest_hash=manifest_hash, smoke_full_k=10, main_commit_sha="", out=str(tmp_path / "conditions.json"),
    )


@pytest.mark.usefixtures("stubbed_environment")
class TestCmdFreezeGuards:
    def test_records_the_environment_read_through_read_environment_record(
        self, tmp_path: Path, monkeypatch, stubbed_environment,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)

        assert run_review_bench.cmd_freeze(args) == 0

        assert json.loads(Path(args.out).read_text())["environment"] == {
            "cli_version": stubbed_environment.cli_version,
            "ambient_config_commit": stubbed_environment.ambient_config_commit,
        }

    def test_records_the_k_it_froze(self, tmp_path: Path, monkeypatch) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)

        assert run_review_bench.cmd_freeze(args) == 0

        assert json.loads(Path(args.out).read_text())["k"] == 10

    def test_fails_without_writing_when_an_arm_directory_is_missing(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        shutil.rmtree(Path(args.arms_root) / arms_mod.ARM_FUNCTION_CONTEXT)

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "missing or holds no files" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_fails_without_writing_when_an_arm_directory_is_empty(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        (Path(args.arms_root) / arms_mod.ARM_CURRENT_RULE / "bench-staff-backend-engineer.md").unlink()

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "missing or holds no files" in capsys.readouterr().err
        assert not Path(args.out).exists()

    @staticmethod
    def _make_the_defect_a_review_round_record(args: argparse.Namespace) -> str:
        """Rewrites defects.json so its one record has `source: review-round`; returns its ID."""
        (record,) = defects.load_confirmed_defects(Path(args.defects_path))
        review_round_record = ConfirmedDefect(**{**record.to_dict(), "id": "rr-1", "source": "review-round"})
        defects.save_confirmed_defects(Path(args.defects_path), [review_round_record])
        return review_round_record.id

    def test_a_review_round_record_with_only_an_szz_shortlist_in_local_exits_2(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        record_id = self._make_the_defect_a_review_round_record(args)
        defects.save_candidates(Path(args.local_dir) / "szz_candidates.json", [_candidate(id="szz-1")])

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert record_id in stderr
        assert "excerpt" in stderr
        assert not Path(args.out).exists()

    @pytest.mark.parametrize("excerpt", ["", "  \n\t "])
    def test_a_review_round_record_whose_local_candidate_has_no_non_empty_excerpt_exits_2(
        self, tmp_path: Path, monkeypatch, capsys, excerpt: str,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        record_id = self._make_the_defect_a_review_round_record(args)
        defects.save_candidates(
            Path(args.local_dir) / "review_round_candidates.json",
            [_candidate(id=record_id, source="review-round", excerpt=excerpt)],
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "excerpt" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_a_review_round_record_with_a_non_empty_local_excerpt_freezes(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        record_id = self._make_the_defect_a_review_round_record(args)
        defects.save_candidates(
            Path(args.local_dir) / "review_round_candidates.json",
            [_candidate(id=record_id, source="review-round", excerpt="A finding about an unrelated cache.")],
        )

        assert run_review_bench.cmd_freeze(args) == 0

    def test_records_the_gating_rule_and_each_gates_fixture_and_defect_counts_without_the_secondary_cells(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """Seven records over all six (source, kind) cells. The fixture is the (base, head) pair, so the
        two `szz` code records share one and the two markdown records share another."""
        args = _prepare_freeze(tmp_path, monkeypatch)
        (record,) = defects.load_confirmed_defects(Path(args.defects_path))
        base_commits = {name: character * 40 for name, character in (("a", "a"), ("b", "c"), ("c", "d"), ("d", "e"), ("e", "f"))}
        cells = [  # (id, source, file_is_markdown, fixture)
            ("szz-code-1", "szz", False, "a"), ("szz-code-2", "szz", False, "a"), ("rr-code", "review-round", False, "b"),
            ("rr-md", "review-round", True, "c"), ("pc-md", "pr-comment", True, "c"),
            ("pc-code", "pr-comment", False, "d"), ("szz-md", "szz", True, "e"),
        ]
        defects.save_confirmed_defects(Path(args.defects_path), [
            ConfirmedDefect(**{
                **record.to_dict(), "id": defect_id, "source": source, "file_is_markdown": file_is_markdown,
                "base_commit": base_commits[fixture],
            })
            for defect_id, source, file_is_markdown, fixture in cells
        ])
        defects.save_candidates(Path(args.local_dir) / "review_round_candidates.json", [
            _candidate(id=defect_id, source="review-round", excerpt="A finding about an unrelated cache.")
            for defect_id in ("rr-code", "rr-md")
        ])

        assert run_review_bench.cmd_freeze(args) == 0

        conditions = json.loads(Path(args.out).read_text())
        assert conditions["gating_rule"] == analysis.gating_rule_record()
        assert conditions["gate_counts"] == {
            "code": {"fixture_count": 2, "defect_count": 3}, "markdown": {"fixture_count": 1, "defect_count": 2},
        }
        stderr = capsys.readouterr().err
        assert "freeze: secondary: 2 fixture(s), 2 defect(s)" in stderr
        assert f"freeze: code: 2 fixture(s), 3 defect(s) -- below N_min(10) = {analysis.n_min(10)}" in stderr

    def test_a_gating_rule_out_of_step_with_known_sources_exits_2_without_writing(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        monkeypatch.setattr(analysis, "GATING_RULE", {**analysis.GATING_RULE, ("pr-body", False): "code"})

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "unknown sources: ['pr-body']" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_a_stale_smoke_manifest_hash_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        args.last_smoke_manifest_hash = "0" * 64

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "does not match the last passing smoke campaign's" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_a_k_that_differs_from_the_smoke_full_k_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        args.smoke_full_k = 5

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "K being frozen (10) does not match the smoke campaign's full-K fixture (5)" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_a_description_sharing_a_word_run_with_a_local_excerpt_exits_2(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        # The excerpt belongs to a different candidate than the defect: the
        # provenance check compares against every shortlisted excerpt.
        defects.save_candidates(
            Path(args.local_dir) / "szz_candidates.json",
            [_candidate(id="szz-other", excerpt="The reviewer said x changed its stale initial value silently.")],
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "d1" in stderr
        assert "shares word run" in stderr
        assert not Path(args.out).exists()

    # --- a pr-comment record's description may repeat the comment text stored in .local/ ---

    _COMMENT_TEXT = "app.py:2 — alpha bravo charlie delta echo foxtrot golf."
    _EXCERPT_REPEATING_THE_COMMENT = "the finding says alpha bravo charlie delta echo foxtrot golf and more"

    def _freeze_args_for_a_record(
        self, tmp_path: Path, monkeypatch, *, source: str, description: str, stored_in_local: bool = True,
        local_source: str | None = None,
    ) -> argparse.Namespace:
        """`freeze` args for one record of `source` with `description`, beside a review-round candidate whose
        excerpt repeats the comment text. `.local/` holds the record's candidate, with its stored comment text,
        unless `stored_in_local` is false. That candidate claims `local_source`, which defaults to `source`."""
        args = _prepare_freeze(tmp_path, monkeypatch)
        (record,) = defects.load_confirmed_defects(Path(args.defects_path))
        defects.save_confirmed_defects(Path(args.defects_path), [ConfirmedDefect(
            **{**record.to_dict(), "id": "comment-1", "source": source, "description": description},
        )])
        local_candidates = [_candidate(
            id="rr-excerpt", source="review-round", excerpt=self._EXCERPT_REPEATING_THE_COMMENT,
        )]
        if stored_in_local:
            local_candidates.append(_candidate(
                id="comment-1", source=local_source or source, evidence={"public_comment_text": self._COMMENT_TEXT},
            ))
        defects.save_candidates(Path(args.local_dir) / "szz_candidates.json", local_candidates)
        return args

    def test_a_verbatim_pr_comment_description_sharing_a_run_with_an_excerpt_freezes(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        args = self._freeze_args_for_a_record(
            tmp_path, monkeypatch, source="pr-comment", description=self._COMMENT_TEXT,
        )

        assert run_review_bench.cmd_freeze(args) == 0

    def test_words_appended_to_a_pr_comment_that_form_an_excerpt_run_exit_2_naming_the_run(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = self._freeze_args_for_a_record(
            tmp_path, monkeypatch, source="pr-comment", description=self._COMMENT_TEXT,
        )
        # An excerpt run the comment text does not hold, which the description now repeats.
        defects.save_candidates(Path(args.local_dir) / "review_round_candidates.json", [_candidate(
            id="rr-second-excerpt", source="review-round", excerpt="one two three four five six seven",
        )])
        (record,) = defects.load_confirmed_defects(Path(args.defects_path))
        defects.save_confirmed_defects(Path(args.defects_path), [ConfirmedDefect(
            **{**record.to_dict(), "description": f"{self._COMMENT_TEXT} one two three four five six"},
        )])

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "comment-1" in stderr
        assert "shares word run 'one two three four five six'" in stderr
        assert not Path(args.out).exists()

    @pytest.mark.parametrize("source", ["review-round", "szz"])
    def test_a_record_outside_the_github_comment_sources_gets_no_exemption_from_the_stored_comment_text(
        self, tmp_path: Path, monkeypatch, capsys, source: str,
    ) -> None:
        args = self._freeze_args_for_a_record(
            tmp_path, monkeypatch, source=source, description=self._COMMENT_TEXT,
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "comment-1" in capsys.readouterr().err

    def test_a_committed_szz_record_gets_no_exemption_from_a_local_candidate_that_claims_pr_comment(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """`.local/` is hand-editable, so the record's own committed `source` decides the exemption."""
        args = self._freeze_args_for_a_record(
            tmp_path, monkeypatch, source="szz", description=self._COMMENT_TEXT, local_source="pr-comment",
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "comment-1" in stderr
        assert "shares word run" in stderr
        assert not Path(args.out).exists()

    def test_a_pr_comment_record_whose_candidate_is_missing_from_local_falls_back_to_git_text_and_exits_2(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        args = self._freeze_args_for_a_record(
            tmp_path, monkeypatch, source="pr-comment", description=self._COMMENT_TEXT, stored_in_local=False,
        )

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "shares word run" in capsys.readouterr().err
        assert not Path(args.out).exists()

    def test_public_git_text_that_cannot_be_read_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        # A repo that holds neither of the defect's commits, so `git show` fails.
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", _init_repo(tmp_path / "unrelated-repo"))

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "d1" in stderr
        assert "could not read public git text" in stderr
        assert not Path(args.out).exists()

    def test_an_szz_only_defect_set_needs_no_local_excerpt(self, tmp_path: Path, monkeypatch) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        (Path(args.local_dir) / "szz_candidates.json").unlink()

        assert run_review_bench.cmd_freeze(args) == 0

    def test_refuses_to_overwrite_an_existing_freeze(self, tmp_path: Path, monkeypatch, capsys) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        Path(args.out).write_text("the earlier freeze")

        exit_code = run_review_bench.cmd_freeze(args)

        assert exit_code == 2
        assert "already exists" in capsys.readouterr().err
        assert Path(args.out).read_text() == "the earlier freeze"

    def test_writes_through_atomic_write_text_creating_the_parent_directory(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        args.out = str(tmp_path / "not-yet-created" / "conditions.json")
        written_paths: list[Path] = []
        real_atomic_write_text = defects.atomic_write_text

        def recording_atomic_write_text(path: Path, text: str) -> None:
            written_paths.append(path)
            real_atomic_write_text(path, text)

        monkeypatch.setattr(defects, "atomic_write_text", recording_atomic_write_text)

        assert run_review_bench.cmd_freeze(args) == 0

        assert written_paths == [Path(args.out)]
        assert list(Path(args.out).parent.glob("*.tmp-*")) == []


class TestBuildSpotCheckSamples:
    def test_joins_reviewer_and_judge_records_by_defect_and_filters_missing_status(self, tmp_path: Path) -> None:
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", "current-rule", "run-a", "Leaks a connection on error."),
            _run_record("d1", "function-context", "run-b", "Nothing to report."),
            # A missing reviewer run never reaches the judge's own input,
            # so it must never surface as a spot-check candidate either. A
            # distinct run_index: it's current-rule's second run in this
            # block, not a rerun of run-a's own (campaign_id, defect_id,
            # arm, run_index) identity, which read_run_records dedups on.
            _run_record("d1", "current-rule", "run-c", "A missing run.", status="missing", run_index=1),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        args = argparse.Namespace(
            reviewer_records_path=str(reviewer_records_path), judge_records_path=str(judge_records_path), seed=0,
        )
        recall_sample, precision_sample = run_review_bench._build_spot_check_samples(args)

        assert {c.item_id for c in recall_sample} == {"d1:run-a", "d1:run-b"}
        assert {c.item_id for c in precision_sample} == {"d1:run-a:0"}

    def test_excludes_judge_record_for_a_defect_with_no_reviewer_record_at_all(self, tmp_path: Path) -> None:
        """A crash between writing a reviewer campaign's own records and a
        later judge campaign's can leave a judge record naming a defect_id
        no reviewer record was ever written for. The join must drop it
        rather than raise or fabricate a candidate with no findings text."""
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", "current-rule", "run-a", "Leaks a connection on error."),
            _run_record("d1", "function-context", "run-b", "Nothing to report."),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
            # "d2" has a judge record but no reviewer record at all.
            _run_record("d2", JUDGE_ARM_RECALL, "j-recall-orphan", "run-z: FOUND -- \"orphaned\""),
            _run_record("d2", JUDGE_ARM_PRECISION, "j-precision-orphan", '### Run run-z\n1. VALID -- "orphaned"\n'),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        args = argparse.Namespace(
            reviewer_records_path=str(reviewer_records_path), judge_records_path=str(judge_records_path), seed=0,
        )
        recall_sample, precision_sample = run_review_bench._build_spot_check_samples(args)

        assert {c.item_id for c in recall_sample} == {"d1:run-a", "d1:run-b"}
        assert {c.item_id for c in precision_sample} == {"d1:run-a:0"}
        assert not any(c.item_id.startswith("d2:") for c in recall_sample + precision_sample)


class TestSpotCheckSheetBlindingAndReproducibility:
    def _write_records(self, tmp_path: Path, defect_ids: tuple[str, ...]) -> argparse.Namespace:
        reviewer_records, judge_records = [], []
        for defect_id in defect_ids:
            reviewer_records += [
                _run_record(
                    defect_id, "current-rule", f"{defect_id}-a", "Leaks a connection, see .bench/change-function-context.diff.",
                ),
                _run_record(defect_id, "function-context", f"{defect_id}-b", "Nothing to report."),
            ]
            judge_records += [
                _run_record(
                    defect_id, JUDGE_ARM_RECALL, f"{defect_id}-recall",
                    f'{defect_id}-a: FOUND -- "Leaks a connection"\n{defect_id}-b: NOT_FOUND',
                ),
                _run_record(
                    defect_id, JUDGE_ARM_PRECISION, f"{defect_id}-precision",
                    f'### Run {defect_id}-a\n1. VALID -- "Leaks a connection"\n### Run {defect_id}-b\n',
                ),
            ]
        reviewer_records_path, judge_records_path = tmp_path / "reviewer.jsonl", tmp_path / "judge.jsonl"
        runner.append_run_records(reviewer_records_path, reviewer_records)
        runner.append_run_records(judge_records_path, judge_records)
        return argparse.Namespace(
            reviewer_records_path=str(reviewer_records_path), judge_records_path=str(judge_records_path), seed=3,
        )

    def test_display_text_replaces_a_bench_artifact_citation_with_the_neutral_token(self, tmp_path: Path) -> None:
        args = self._write_records(tmp_path, ("d1",))

        recall_sample, precision_sample = run_review_bench._build_spot_check_samples(args)

        display_texts = [candidate.display_text for candidate in recall_sample + precision_sample]
        assert display_texts
        assert all(".bench/" not in text and "change-function-context.diff" not in text for text in display_texts)
        assert any("[bench file]" in text for text in display_texts)

    def test_the_same_records_and_seed_sample_the_same_items_each_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from review_bench import adjudicate

        monkeypatch.setattr(adjudicate, "SPOT_CHECK_RECALL_SAMPLE_SIZE", 3)
        monkeypatch.setattr(adjudicate, "SPOT_CHECK_PRECISION_SAMPLE_SIZE", 2)
        args = self._write_records(tmp_path, tuple(f"d{i}" for i in range(8)))

        first_recall, first_precision = run_review_bench._build_spot_check_samples(args)
        second_recall, second_precision = run_review_bench._build_spot_check_samples(args)

        assert len(first_recall) == 3 and len(first_precision) == 2
        assert [c.item_id for c in first_recall] == [c.item_id for c in second_recall]
        assert [c.item_id for c in first_precision] == [c.item_id for c in second_precision]


def _confirmed_single_defect(
    defect_id: str = "d1", *, source: str = "szz", file_is_markdown: bool = False, fixture: str | None = None,
) -> ConfirmedDefect:
    """A defect on a fixture named `fixture`, which defaults to a fixture of its own so
    that a cluster bootstrap resamples per defect."""
    head_commit = hashlib.sha1((fixture or defect_id).encode()).hexdigest()
    return ConfirmedDefect(
        id=defect_id, source=source, lens="staff-backend-engineer", base_commit="a" * 40, head_commit=head_commit,
        fix_commit="b" * 40, fix_date="2024-01-01", description="test defect",
        path="notes.md" if file_is_markdown else "app.py", file_is_markdown=file_is_markdown,
    )


def _run_or_smoke_args(tmp_path: Path, files: dict[str, Path], *, subcommand: str) -> argparse.Namespace:
    """`files` comes from `_frozen_files` at K=1, the K these arguments carry."""
    return argparse.Namespace(
        subcommand=subcommand, defects_path=str(files["defects"]), defect_id=[], arms_root=str(files["arms"]),
        run_store_dir=str(tmp_path / "run-store"), records_dir=str(tmp_path / "runs"), campaign_id="c1",
        k=1, seed=0, workers=1, inject_fault=None, conditions_path=str(files["conditions"]),
    )


def _defects_and_arms_flags(files: dict[str, Path]) -> list[str]:
    return ["--defects-path", str(files["defects"]), "--arms-root", str(files["arms"])]


def _frozen_conditions_flags(files: dict[str, Path]) -> list[str]:
    """The flags that point `run` at `_frozen_files`' defects, arms, and conditions."""
    return [*_defects_and_arms_flags(files), "--conditions-path", str(files["conditions"])]


@pytest.mark.usefixtures("stubbed_environment")
class TestRunAndSmokeOutOfSessionReporting:
    """`run` and `smoke` share `_run_or_smoke`, so its out-of-session
    reporting is exercised for both public entry points."""

    def _stub_run_campaign(self, monkeypatch) -> runner.RunRecord:
        record = _run_record(
            "d1", arms_mod.ARM_CURRENT_RULE, "run-a", "finding", out_of_session_paths=("secret/path.txt",),
        )
        block_result = runner.BlockResult(
            records=(record,), representative_session_id_by_arm={arms_mod.ARM_CURRENT_RULE: "sess-1"},
        )

        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            return runner.CampaignResult(campaign_id=campaign_id, block_results={"d1": block_result})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        return record

    def test_run_prints_each_out_of_session_path_and_the_per_arm_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        record = self._stub_run_campaign(monkeypatch)

        exit_code = run_review_bench.cmd_run(_run_or_smoke_args(tmp_path, files, subcommand="run"))

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"run: out-of-session read in {arms_mod.ARM_CURRENT_RULE} run run-a: secret/path.txt" in stderr

        expected_counts = analysis.out_of_session_counts_by_arm([record])
        assert f"run: out-of-session read counts per arm = {expected_counts}" in stderr

    def test_smoke_prints_each_out_of_session_path_and_the_per_arm_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        record = self._stub_run_campaign(monkeypatch)

        exit_code = run_review_bench.cmd_smoke(_run_or_smoke_args(tmp_path, files, subcommand="smoke"))

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"smoke: out-of-session read in {arms_mod.ARM_CURRENT_RULE} run run-a: secret/path.txt" in stderr

        expected_counts = analysis.out_of_session_counts_by_arm([record])
        assert f"smoke: out-of-session read counts per arm = {expected_counts}" in stderr


class TestSmokeManifestHashIsTakenBeforeDispatch:
    def test_smoke_prints_the_closure_hash_from_before_the_campaign(
        self, monkeypatch, capsys,
    ) -> None:
        hash_before_campaign = analysis.closure_manifest_hash(analysis.compute_harness_closure())
        closure_after_edit = analysis.compute_harness_closure() | {"edited-during-campaign.py": "0" * 64}
        assert analysis.closure_manifest_hash(closure_after_edit) != hash_before_campaign

        def edit_the_harness_then_succeed(*args, **kwargs) -> int:
            monkeypatch.setattr(analysis, "compute_harness_closure", lambda: closure_after_edit)
            return 0

        monkeypatch.setattr(run_review_bench, "_run_or_smoke", edit_the_harness_then_succeed)
        smoke_args = argparse.Namespace(subcommand="smoke", inject_fault=None, k=1)

        exit_code = run_review_bench.cmd_smoke(smoke_args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"smoke: harness closure manifest hash = {hash_before_campaign} (K=1)" in stderr


class TestModelEmittedPathsAreEscapedForTheTerminal:
    @pytest.mark.parametrize(
        ("raw_text", "escaped_text"),
        [
            ("\x1b[31mred\x1b[0m", "\\x1b[31mred\\x1b[0m"),
            ("a\rb\nc\td", "a\\rb\\nc\\td"),
            ("csi\x9b2J", "csi\\x9b2J"),
            ("bidi\N{RIGHT-TO-LEFT OVERRIDE}evil", "bidi\\u202eevil"),
            ("line\N{LINE SEPARATOR}sep", "line\\u2028sep"),
            ("para\N{PARAGRAPH SEPARATOR}sep", "para\\u2029sep"),
            ("private\ue000use", "private\\ue000use"),
            ("nonchar\uffffend", "nonchar\\uffffend"),
            ("lone\ud800surrogate", "lone\\ud800surrogate"),
            ("café/notes.py", "café/notes.py"),
            ("plain/path.py", "plain/path.py"),
        ],
    )
    def test_control_format_and_separator_characters_become_backslash_escapes(
        self, raw_text: str, escaped_text: str,
    ) -> None:
        assert run_review_bench._escape_control_characters(raw_text) == escaped_text

    def test_a_recorded_path_with_an_escape_sequence_never_reaches_stderr_raw(self, capsys) -> None:
        record = _run_record(
            "d1", arms_mod.ARM_CURRENT_RULE, "run-a", "finding", out_of_session_paths=("/x\x1b]0;pwned\x07",),
        )
        run_review_bench._print_out_of_session_reads("analyze", [record])
        stderr = capsys.readouterr().err
        assert "\x1b" not in stderr
        assert "\x07" not in stderr
        assert "analyze: out-of-session read in current-rule run run-a: /x\\x1b]0;pwned\\x07" in stderr


@pytest.mark.usefixtures("stubbed_environment")
class TestRunAndSmokeEnvironmentMismatchHalt:
    """`run` and `smoke` share `_run_or_smoke`'s `runner.EnvironmentMismatchError`
    catch, so both public entry points must surface a halted block as exit
    code 2, not a traceback."""

    def _stub_run_campaign_raising_mismatch(self, monkeypatch, message: str) -> None:
        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            raise runner.EnvironmentMismatchError(message)

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)

    def test_run_returns_exit_code_2_and_prints_the_error_to_stderr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        self._stub_run_campaign_raising_mismatch(monkeypatch, "halted: d1's block end differs")

        exit_code = run_review_bench.cmd_run(_run_or_smoke_args(tmp_path, files, subcommand="run"))

        assert exit_code == 2
        assert "halted: d1's block end differs" in capsys.readouterr().err

    def test_smoke_returns_exit_code_2_and_prints_the_error_to_stderr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        self._stub_run_campaign_raising_mismatch(monkeypatch, "halted: d1's block end differs")

        exit_code = run_review_bench.cmd_smoke(_run_or_smoke_args(tmp_path, files, subcommand="smoke"))

        assert exit_code == 2
        assert "halted: d1's block end differs" in capsys.readouterr().err


@pytest.mark.usefixtures("stubbed_environment")
class TestRunAndSmokeCampaignSelection:
    """`smoke` then `run` on the CLI's own defaults must not share completion
    state: `RunStore.completed_block_ids` is keyed by defect ID alone, so a
    shared default store makes `run` skip every defect `smoke` completed."""

    def _stub_blocks(self, monkeypatch, tmp_path: Path) -> list[str]:
        monkeypatch.setattr(run_review_bench, "DEFAULT_RUN_STORE_DIR", tmp_path / "default-run-store")
        monkeypatch.setattr(run_review_bench, "_load_defects_for_run", lambda args: [_confirmed_single_defect()])
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        monkeypatch.setattr(
            runner, "build_defect_fixture_spec",
            lambda defect, **kwargs: runner.DefectFixtureSpec(
                defect_id=defect.id, arm_fixture_dirs={}, arm_agent_names={},
                agent_declared_tools=frozenset(), live_checkout_roots=(), changed_relpaths=(), over_read_cap=False,
            ),
        )
        blocks_run: list[str] = []

        def fake_run_defect_block(spec, *, campaign_id, **kwargs):
            blocks_run.append(f"{campaign_id}:{spec.defect_id}")
            return runner.BlockResult(records=(), representative_session_id_by_arm={})

        monkeypatch.setattr(runner, "run_defect_block", fake_run_defect_block)
        return blocks_run

    def test_run_after_smoke_on_default_paths_still_runs_the_smoked_defect(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        blocks_run = self._stub_blocks(monkeypatch, tmp_path)
        files = _frozen_files(tmp_path, monkeypatch, k=runner.DEFAULT_K)
        parser = run_review_bench.build_parser()

        smoke_args = parser.parse_args(["smoke", "--defect-id", "d1", "--records-dir", str(tmp_path / "runs")])
        assert run_review_bench.cmd_smoke(smoke_args) == 0
        run_args = parser.parse_args(
            ["run", "--records-dir", str(tmp_path / "runs"), *_frozen_conditions_flags(files)],
        )
        assert run_review_bench.cmd_run(run_args) == 0

        assert len(blocks_run) == 2
        assert blocks_run[0].startswith("smoke-") and blocks_run[1].startswith("run-")

    def test_resuming_one_campaign_id_skips_its_own_completed_defect(self, tmp_path: Path, monkeypatch) -> None:
        blocks_run = self._stub_blocks(monkeypatch, tmp_path)
        files = _frozen_files(tmp_path, monkeypatch, k=runner.DEFAULT_K)
        parser = run_review_bench.build_parser()
        argv = [
            "run", "--campaign-id", "resume-me", "--records-dir", str(tmp_path / "runs"),
            *_frozen_conditions_flags(files),
        ]

        assert run_review_bench.cmd_run(parser.parse_args(argv)) == 0
        assert run_review_bench.cmd_run(parser.parse_args(argv)) == 0

        assert blocks_run == ["resume-me:d1"]

    @pytest.mark.parametrize("subcommand", ["run", "smoke"])
    @pytest.mark.parametrize("bad_k", ["0", "-1", "abc"])
    def test_rejects_a_k_below_one_at_parse_time(self, subcommand: str, bad_k: str, capsys) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.build_parser().parse_args([subcommand, "--defect-id", "d1", "--k", bad_k])

        assert exit_info.value.code == 2

    @pytest.mark.parametrize("subcommand", ["run", "smoke"])
    def test_prints_the_campaign_id_and_store_before_taking_the_lock(
        self, subcommand: str, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        self._stub_blocks(monkeypatch, tmp_path)

        def crash_on_lock(self):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(runner.RunStore, "acquire_lock", crash_on_lock)
        args = run_review_bench.build_parser().parse_args(
            [subcommand, "--defect-id", "d1", "--records-dir", str(tmp_path / "runs")],
        )

        with pytest.raises(RuntimeError):
            run_review_bench._run_or_smoke(args, fault=None, verify_frozen=False)

        stderr = capsys.readouterr().err
        assert f"{subcommand}: campaign {subcommand}-" in stderr
        assert "run store at" in stderr

    @pytest.mark.parametrize("subcommand", ["run", "smoke"])
    @pytest.mark.parametrize("campaign_id", ["../escape", "/abs/path", "a/b", "-x", ""])
    def test_rejects_a_malformed_campaign_id_before_creating_any_store(
        self, subcommand: str, campaign_id: str, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        self._stub_blocks(monkeypatch, tmp_path)

        exit_code = run_review_bench.main([
            subcommand, "--defect-id", "d1", f"--campaign-id={campaign_id}", "--records-dir", str(tmp_path / "runs"),
        ])

        assert exit_code == 2
        assert "invalid campaign ID" in capsys.readouterr().err
        assert not (tmp_path / "default-run-store").exists()

    def test_judge_rejects_a_malformed_campaign_id(self, tmp_path: Path, capsys) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        exit_code = run_review_bench.main([
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_records_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"), "--campaign-id", "../escape",
        ])

        assert exit_code == 2
        assert "invalid campaign ID" in capsys.readouterr().err


@pytest.mark.usefixtures("stubbed_environment")
class TestRunAndSmokeFlagsReachRunCampaign:
    """The campaign flags these tests name (`--inject-fault`, `--seed`, `--k`,
    `--workers`, `--campaign-id`, `--defect-id`) reach `run_campaign` as the
    argument the harness acts on, beside the environment reference built for
    the subcommand. Live-checkout roots reach `build_defect_fixture_spec`
    through `build_spec`."""

    def _capture_run_campaign(
        self, monkeypatch, tmp_path: Path, defect_ids=("d1", "d2"), frozen_k: int = runner.DEFAULT_K,
        frozen_seed: int = 0,
    ) -> dict:
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=defect_ids, k=frozen_k, campaign_seed=frozen_seed)
        captured: dict = {"files": files}

        def fake_run_campaign(selected_defect_ids, **kwargs):
            captured["defect_ids"] = list(selected_defect_ids)
            captured.update(kwargs)
            return runner.CampaignResult(campaign_id=kwargs["campaign_id"], block_results={})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        return captured

    def _argv(self, subcommand: str, captured: dict, tmp_path: Path, *extra: str) -> list[str]:
        path_flags = (
            _frozen_conditions_flags(captured["files"]) if subcommand == "run"
            else _defects_and_arms_flags(captured["files"])
        )
        return [
            subcommand, *path_flags, "--records-dir", str(tmp_path / "runs"),
            "--run-store-dir", str(tmp_path / "run-store"), *extra,
        ]

    def test_smoke_inject_fault_reaches_run_campaign(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path)
        argv = self._argv("smoke", captured, tmp_path, "--defect-id", "d1", "--inject-fault", "wrong-agent")

        assert run_review_bench.main(argv) == 0

        assert captured["fault"] == "wrong-agent"

    def test_run_reaches_run_campaign_with_no_fault(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path)

        assert run_review_bench.main(self._argv("run", captured, tmp_path)) == 0

        assert captured["fault"] is None

    def test_run_hands_the_live_checkout_roots_to_every_defect_fixture_spec(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        live_roots = (Path("/live-checkout-sentinel"),)
        monkeypatch.setattr(runner, "default_live_checkout_roots", lambda: live_roots)
        fixture_spec_kwargs: dict = {}

        def fake_build_defect_fixture_spec(defect, **kwargs):
            fixture_spec_kwargs.update(kwargs)

        monkeypatch.setattr(runner, "build_defect_fixture_spec", fake_build_defect_fixture_spec)
        captured = self._capture_run_campaign(monkeypatch, tmp_path)

        assert run_review_bench.main(self._argv("run", captured, tmp_path)) == 0
        captured["build_spec"]("d1")

        assert fixture_spec_kwargs["live_checkout_roots"] == live_roots

    def test_run_holds_every_block_to_the_frozen_environment_and_smoke_to_its_first_reading(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path)
        other_environment = runner.EnvironmentRecord("9.9.9", "cafebabe")

        assert run_review_bench.main(self._argv("run", captured, tmp_path)) == 0
        with pytest.raises(runner.EnvironmentMismatchError, match="frozen environment"):
            captured["environment_reference"].require_match(other_environment, where="d1's block start")

        assert run_review_bench.main(self._argv("smoke", captured, tmp_path, "--defect-id", "d1")) == 0
        captured["environment_reference"].require_match(other_environment, where="d1's block start")  # adopted

    def test_seed_k_workers_and_campaign_id_reach_run_campaign(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path, frozen_k=3, frozen_seed=7)
        argv = self._argv(
            "run", captured, tmp_path, "--seed", "7", "--k", "3", "--workers", "2", "--campaign-id", "camp-1",
        )

        assert run_review_bench.main(argv) == 0

        assert (captured["seed"], captured["k"], captured["workers"], captured["campaign_id"]) == (7, 3, 2, "camp-1")
        assert captured["records_path"] == tmp_path / "runs" / "camp-1.jsonl"
        assert captured["run_store"].store_dir == tmp_path / "run-store"

    def test_run_exits_2_before_any_dispatch_when_seed_differs_from_the_frozen_campaign_seed(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path, frozen_seed=7)

        exit_code = run_review_bench.main(self._argv("run", captured, tmp_path, "--seed", "8"))

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "--seed 8 differs from the frozen campaign_seed 7 -- pass --seed 7" in stderr
        assert "rerun all arms" not in stderr
        assert "campaign_id" not in captured

    def test_smoke_does_not_hold_its_seed_to_a_frozen_value(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path, frozen_seed=7)

        assert run_review_bench.main(self._argv("smoke", captured, tmp_path, "--defect-id", "d1", "--seed", "8")) == 0

        assert captured["seed"] == 8

    def test_defect_id_restricts_the_campaign_to_that_defect(self, tmp_path: Path, monkeypatch) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path)

        assert run_review_bench.main(self._argv("run", captured, tmp_path, "--defect-id", "d2")) == 0

        assert captured["defect_ids"] == ["d2"]

    def test_a_defect_id_matching_no_confirmed_defect_is_named_on_stderr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        captured = self._capture_run_campaign(monkeypatch, tmp_path)

        exit_code = run_review_bench.main(
            self._argv("run", captured, tmp_path, "--defect-id", "d2", "--defect-id", "d-typo"),
        )

        assert exit_code == 0
        assert captured["defect_ids"] == ["d2"]
        assert "d-typo" in capsys.readouterr().err

    def test_run_rejects_inject_fault_with_exit_code_2(self, tmp_path: Path, capsys) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.build_parser().parse_args(["run", "--inject-fault", "wrong-agent"])
        assert exit_info.value.code == 2
        assert "--inject-fault" in capsys.readouterr().err


class TestMineSzzBaseRefValidation:
    @pytest.mark.parametrize("base_ref", ["--output=/tmp/x", "-n", "a..b", ""])
    def test_rejects_a_base_ref_git_could_read_as_an_option_before_running_git(
        self, base_ref: str, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import mine_szz

        def fail_if_git_runs(*args, **kwargs):
            raise AssertionError("git must not run for a rejected base ref")

        monkeypatch.setattr(mine_szz, "_run_git", fail_if_git_runs)

        exit_code = run_review_bench.main(["mine-szz", f"--base-ref={base_ref}", "--local-dir", str(tmp_path)])

        assert exit_code == 2
        assert "invalid base ref" in capsys.readouterr().err


class TestCmdJudgeCampaignIdEchoedBeforeLock:
    def test_prints_the_resolved_campaign_id_and_store_dir_before_acquiring_the_lock(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """Regression guard: the resolved --campaign-id must be printed
        before the lock is acquired, so a crash before completion still
        leaves the operator a store name to resume."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        def raise_before_any_work(self, *a, **k):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(runner.RunStore, "acquire_lock", raise_before_any_work)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            conditions_path=str(tmp_path / "no-conditions.json"), arms_root=str(tmp_path / "arms"),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id=None, seed=0,
        )

        with pytest.raises(RuntimeError):
            run_review_bench.cmd_judge(args)

        stderr = capsys.readouterr().err
        assert "judge: campaign judge-" in stderr
        assert "run store at" in stderr


class TestCmdJudgeOutOfSessionReporting:
    def test_prints_each_out_of_session_path_and_the_per_judge_kind_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", "current-rule", "d1-run", "finding")])

        captured_judge_records: list[runner.RunRecord] = []

        def fake_run_defect_judges(defect, records, **kwargs):
            recall = _run_record(
                defect.id, JUDGE_ARM_RECALL, "j-recall", "r1: NOT_FOUND", out_of_session_paths=("secret/path.txt",),
            )
            precision = _run_record(defect.id, JUDGE_ARM_PRECISION, "j-precision", "### Run r1\n")
            captured_judge_records.extend((recall, precision))
            return recall, precision

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        args = argparse.Namespace(
            defects_path=str(defects_path), defect_id=[], reviewer_records_path=str(reviewer_records_path),
            conditions_path=str(tmp_path / "no-conditions.json"), arms_root=str(tmp_path / "arms"),
            judge_run_store_dir=str(tmp_path / "judge-run-store"), judge_records_dir=str(tmp_path / "judge-runs"),
            campaign_id="c1", seed=0,
        )

        exit_code = run_review_bench.cmd_judge(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"judge: out-of-session read in {JUDGE_ARM_RECALL} run j-recall: secret/path.txt" in stderr

        expected_counts = analysis.out_of_session_counts_by_arm(captured_judge_records)
        assert f"out-of-session read counts per judge kind = {expected_counts}" in stderr


class TestCmdAnalyzeOutOfSessionReport:
    def test_written_report_carries_a_populated_per_judge_kind_count(
        self, tmp_path: Path, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "Leaks a connection on error."),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "Nothing to report."),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
                out_of_session_paths=("secret/path.txt",),
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path), arms_root=str(_arms_root_with_both_arm_snapshots(tmp_path)),
        )

        exit_code = run_review_bench.cmd_analyze(args)

        assert exit_code == 0
        report_text = out_path.read_text()
        report = json.loads(report_text)
        assert report["out_of_session_read_counts_per_judge_kind"] == {JUDGE_ARM_RECALL: 1, JUDGE_ARM_PRECISION: 0}
        assert "secret/path.txt" not in report_text

        # The raw path is terminal-only -- the committed --out report above
        # carries only the count.
        stderr = capsys.readouterr().err
        assert f"analyze: out-of-session read in {JUDGE_ARM_RECALL} run j-recall: secret/path.txt" in stderr


class TestCmdAnalyzeMalformedBaselineConditions:
    """Mirrors TestRunAndSmokeEnvironmentMismatchHalt's contract: a
    designed invalidation path must surface as exit code 2, not a raw
    traceback, for a missing file, for each way the file's content can fail
    to parse into the expected shape, and for a present-but-wrong-typed
    harness_closure field."""

    def _args(self, tmp_path: Path, defects_path: Path, baseline_conditions_path: Path) -> argparse.Namespace:
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [_run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "")])
        judge_records_path = tmp_path / "judge.jsonl"
        judge_records_path.touch()
        return argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None,
            baseline_conditions_path=str(baseline_conditions_path), out=str(tmp_path / "report.json"),
        )

    def test_invalid_json_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text("not json")

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_missing_environment_key_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text(json.dumps({"harness_closure": {}}))

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_non_dict_top_level_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        """A top-level JSON array parses without error, so this is the
        TypeError branch, not the KeyError branch the other two cases hit."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text(json.dumps(["not", "a", "dict"]))

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_non_dict_harness_closure_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        """A non-dict harness_closure value, with an otherwise well-formed
        environment object, is caught at extraction time -- this is the
        wrong-shaped-value branch, not the missing-key branch the
        environment-only case above hits."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "conditions.json"
        baseline_conditions_path.write_text(json.dumps({
            "environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"},
            "harness_closure": "corrupt",
        }))

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr

    def test_nonexistent_path_returns_exit_code_2_and_names_the_path(self, tmp_path: Path, capsys) -> None:
        """A typo'd CLI arg hits this branch: read_text() raises
        FileNotFoundError, an OSError subclass, before json.loads ever runs."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        baseline_conditions_path = tmp_path / "does-not-exist.json"

        exit_code = run_review_bench.cmd_analyze(
            self._args(tmp_path, defects_path, baseline_conditions_path),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert str(baseline_conditions_path) in stderr
        assert "unreadable or missing an expected field" in stderr


class TestCmdAnalyzeReportCompleteness:
    def test_written_report_carries_the_never_gating_secondary_columns(self, tmp_path: Path) -> None:
        """Asserts the wiring: each secondary column listed below is present in the --out
        report, since a per-function unit test cannot catch cmd_analyze never
        calling one of them. Only d2 is over_read_cap, so the over-cap
        stratum's recall diff is +1.0 and a broken over_read_cap extraction
        would give 0.0, which a bare key-presence check would miss."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(
            defects_path, [_confirmed_single_defect("d1"), _confirmed_single_defect("d2")],
        )

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        reviewer_records = [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "Leaks a connection on error."),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "Nothing to report."),
            _run_record("d2", arms_mod.ARM_CURRENT_RULE, "run-c", "Nothing to report.", over_read_cap=True),
            _run_record("d2", arms_mod.ARM_FUNCTION_CONTEXT, "run-d", "Leaks a null pointer.", over_read_cap=True),
        ]
        runner.append_run_records(reviewer_records_path, reviewer_records)

        judge_records_path = tmp_path / "judge.jsonl"
        judge_records = [
            _run_record(
                "d1", JUDGE_ARM_RECALL, "j-recall-1", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND',
            ),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision-1",
                '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
            _run_record(
                "d2", JUDGE_ARM_RECALL, "j-recall-2",
                'run-c: NOT_FOUND\nrun-d: FOUND -- "Leaks a null pointer"',
            ),
            _run_record("d2", JUDGE_ARM_PRECISION, "j-precision-2", "### Run run-c\n### Run run-d\n"),
        ]
        runner.append_run_records(judge_records_path, judge_records)

        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path), arms_root=str(_arms_root_with_both_arm_snapshots(tmp_path)),
        )

        exit_code = run_review_bench.cmd_analyze(args)

        assert exit_code == 0
        report = json.loads(out_path.read_text())
        # The never-gating secondary columns the report carries.
        for column in (
            "read_tokens_per_arm", "partial_and_paged_counts_per_arm", "whole_file_read_adherence_per_arm",
            "missing_runs_by_reason_per_arm", "out_of_session_read_counts_per_arm",
            "out_of_session_read_counts_per_judge_kind", "recall_by_fix_date_half_per_arm",
            "recall_diff_over_read_cap_stratum", "observed_sigma_d",
        ):
            assert column in report, f"cmd_analyze's --out report is missing the {column!r} secondary column"
        # d2 is the sole over_read_cap defect and is found only in
        # function-context (ARM_FUNCTION_CONTEXT - ARM_CURRENT_RULE = 1 - 0).
        assert report["recall_diff_over_read_cap_stratum"] == 1.0
        # The fix-date halves and sigma_d are per gate, and both defects are in the code gate.
        assert sorted(report["recall_by_fix_date_half_per_arm"]) == ["code", "markdown"]
        assert report["recall_by_fix_date_half_per_arm"]["markdown"][arms_mod.ARM_CURRENT_RULE] == {
            "earlier_half": None, "later_half": None,
        }
        # The sample stdev of d1's and d2's recall differences, -1 and +1.
        assert report["observed_sigma_d"]["code"] == pytest.approx(math.sqrt(2))
        assert report["observed_sigma_d"]["markdown"] is None

    def test_a_baseline_report_carries_the_freeze_identity_a_later_arm_compares(self, tmp_path: Path) -> None:
        """The identity is the closure hash, every frozen digest, K, and the campaign environment the
        reviewer records carry, so a re-freeze that edits only the harness still refuses the report."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect("d1")])
        arms_root = _arms_root_with_both_arm_snapshots(tmp_path)
        reviewer_records_path, judge_records_path = _judged_records(tmp_path)
        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path), arms_root=str(arms_root),
        )

        assert run_review_bench.cmd_analyze(args) == 0

        identity = json.loads(out_path.read_text())["freeze_identity"]
        frozen_fields = analysis.compute_frozen_fields(defects_path, arms_root)
        assert identity == {
            "harness_closure_hash": frozen_fields["harness_closure_hash"],
            **{field: frozen_fields[field] for field in analysis._FROZEN_DIGEST_FIELDS},
            "k": 1,
            "environment": {"cli_version": "2.0.0", "ambient_config_commit": "deadbeef"},
        }

    def test_a_baseline_analysis_with_an_unhashable_arm_directory_exits_2_before_writing_a_report(
        self, tmp_path: Path, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect("d1")])
        reviewer_records_path, judge_records_path = _judged_records(tmp_path)
        out_path = tmp_path / "report.json"
        args = argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=1, arm_x=None, baseline_conditions_path=None,
            out=str(out_path), arms_root=str(tmp_path / "no-arms"),
        )

        assert run_review_bench.cmd_analyze(args) == 2

        assert "missing or holds no files" in capsys.readouterr().err
        assert not out_path.exists()


def _keys_at_every_depth(node) -> list[str]:
    if isinstance(node, dict):
        return [str(key) for key in node] + [key for value in node.values() for key in _keys_at_every_depth(value)]
    if isinstance(node, list):
        return [key for value in node for key in _keys_at_every_depth(value)]
    return []


class TestCmdAnalyzeBaselineReport:
    """Baseline-mode `analyze --out` over a counts table small enough to compute by
    hand, K = 2 (so a defect needs 1 completed run per arm to stay in).

    d1: arm-1 runs a1, a2 and arm-2 runs b1, b2. d2: arm-1 runs c1, c2 and arm-2 runs e1, e2.
    Recall, FOUND / completed: d1 arm 1 = 2/2, arm 2 = 1/2. d2 arm 1 = 1/2, arm 2 = 0/2.
      arm 1 recall = (1.0 + 0.5) / 2 = 0.75. arm 2 recall = (0.5 + 0.0) / 2 = 0.25.
    Precision, VALID / findings: d1 arm 1 = 2/3, arm 2 = 0/1. d2 arm 1 = 1/2, arm 2 = 1/3.
      arm 1 pooled = 3/5 = 0.6. arm 2 pooled = 1/4 = 0.25. Difference = -0.35.
    With two defects, a paired bootstrap resample is (d1, d1), (d1, d2), or (d2, d2), with
    probabilities 1/4, 1/2, 1/4, so each interval's two ends are its (d1, d1) and (d2, d2) resample values.
    """

    _REVIEWER_TEXT = {
        "a1": "Leaks a connection on error. Swallows the exception silently.",
        "a2": "Leaks a connection on error.",
        "b1": "Misnames a variable.",
        "b2": "Nothing to report.",
        "c1": "Null pointer dereference.",
        "c2": "Unused import.",
        "e1": "Wrong loop bound. Missing docstring.",
        "e2": "Null pointer dereference.",
    }
    _RECALL_ANSWERS = {
        "d1": 'a1: FOUND -- "Leaks a connection"\na2: FOUND -- "Leaks a connection"\n'
              'b1: FOUND -- "Misnames a variable"\nb2: NOT_FOUND',
        "d2": 'c1: FOUND -- "Null pointer"\nc2: NOT_FOUND\ne1: NOT_FOUND\ne2: NOT_FOUND',
    }
    _PRECISION_ANSWERS = {
        "d1": '### Run a1\n1. VALID -- "Leaks a connection"\n2. INVALID -- "Swallows the exception"\n'
              '### Run a2\n1. VALID -- "Leaks a connection"\n'
              '### Run b1\n1. INVALID -- "Misnames a variable"\n### Run b2\n',
        "d2": '### Run c1\n1. VALID -- "Null pointer"\n### Run c2\n1. INVALID -- "Unused import"\n'
              '### Run e1\n1. INVALID -- "Wrong loop bound"\n2. INVALID -- "Missing docstring"\n'
              '### Run e2\n1. VALID -- "Null pointer"\n',
    }
    _ARM_BY_RUN = {
        "a1": (arms_mod.ARM_CURRENT_RULE, 0), "a2": (arms_mod.ARM_CURRENT_RULE, 1),
        "b1": (arms_mod.ARM_FUNCTION_CONTEXT, 0), "b2": (arms_mod.ARM_FUNCTION_CONTEXT, 1),
        "c1": (arms_mod.ARM_CURRENT_RULE, 0), "c2": (arms_mod.ARM_CURRENT_RULE, 1),
        "e1": (arms_mod.ARM_FUNCTION_CONTEXT, 0), "e2": (arms_mod.ARM_FUNCTION_CONTEXT, 1),
    }
    _DEFECT_BY_RUN = {"a1": "d1", "a2": "d1", "b1": "d1", "b2": "d1", "c1": "d2", "c2": "d2", "e1": "d2", "e2": "d2"}

    def _write_table(
        self, tmp_path: Path, *, defect_ids=("d1", "d2"), cost_by_run: dict[str, float] | None = None,
        recall_judge_cost_usd: float | None = None, with_precision_judges: bool = True,
    ) -> argparse.Namespace:
        """Writes the table for `defect_ids`. A run absent from `cost_by_run` is unpriced."""
        import dataclasses

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect(d) for d in defect_ids])
        reviewer_records = []
        for run, text in self._REVIEWER_TEXT.items():
            arm, run_index = self._ARM_BY_RUN[run]
            if self._DEFECT_BY_RUN[run] in defect_ids:
                reviewer_records.append(dataclasses.replace(
                    _run_record(self._DEFECT_BY_RUN[run], arm, run, text, run_index=run_index),
                    total_cost_usd=(cost_by_run or {}).get(run),
                ))
        judge_records = []
        for defect_id in defect_ids:
            judge_records.append(dataclasses.replace(
                _run_record(defect_id, JUDGE_ARM_RECALL, f"{defect_id}-recall", self._RECALL_ANSWERS[defect_id]),
                total_cost_usd=recall_judge_cost_usd,
            ))
            if with_precision_judges:
                judge_records.append(_run_record(
                    defect_id, JUDGE_ARM_PRECISION, f"{defect_id}-precision", self._PRECISION_ANSWERS[defect_id],
                ))
        reviewer_records_path, judge_records_path = tmp_path / "reviewer.jsonl", tmp_path / "judge.jsonl"
        runner.append_run_records(reviewer_records_path, reviewer_records)
        runner.append_run_records(judge_records_path, judge_records)
        return argparse.Namespace(
            defects_path=str(defects_path), reviewer_records_path=str(reviewer_records_path),
            judge_records_path=str(judge_records_path), k=2, arm_x=None, baseline_conditions_path=None,
            out=str(tmp_path / "report.json"), arms_root=str(_arms_root_with_both_arm_snapshots(tmp_path)),
        )

    def test_writes_every_listed_item_with_its_hand_computed_value(self, tmp_path: Path) -> None:
        args = self._write_table(tmp_path)

        assert run_review_bench.cmd_analyze(args) == 0

        report = json.loads(Path(args.out).read_text())
        current_rule, function_context = arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT
        assert report["confirmed_defects"] == 2
        assert report["kept_defect_ids"] == ["d1", "d2"]
        assert report["n_min"] == analysis.n_min(2)
        assert report["freeze_identity"]["k"] == 2
        assert report["freeze_identity"]["defects_json_hash"] == (
            hashlib.sha256(Path(args.defects_path).read_bytes()).hexdigest()
        )
        assert not {"k", "defects_json_hash"} & set(report)  # the identity is the only home of both
        code_gate = report["gates"]["code"]
        assert code_gate["effective_n"] == 2
        assert code_gate["fixture_count"] == 2
        assert code_gate["effective_n_meets_n_min"] is False
        assert (report["dropped_defects_recall"], report["dropped_defects_precision"]) == (0, 0)
        assert code_gate["precision_effective_n"] == 2
        assert report["detection_rate_per_defect"] == {
            "d1": {current_rule: 1.0, function_context: 0.5}, "d2": {current_rule: 0.5, function_context: 0.0},
        }
        recall = code_gate["recall_per_arm"]
        assert recall[current_rule]["recall"] == pytest.approx(0.75)
        assert recall[current_rule]["interval"] == pytest.approx([0.5, 1.0])
        assert recall[function_context]["recall"] == pytest.approx(0.25)
        assert recall[function_context]["interval"] == pytest.approx([0.0, 0.5])
        precision = code_gate["pooled_precision_per_arm"]
        assert precision[current_rule]["precision"] == pytest.approx(0.6)
        assert precision[current_rule]["interval"] == pytest.approx([0.5, 2 / 3])
        assert precision[function_context]["precision"] == pytest.approx(0.25)
        assert precision[function_context]["interval"] == pytest.approx([0.0, 1 / 3])
        difference = code_gate["precision_difference"]
        assert (difference["arm"], difference["minus_arm"]) == (function_context, current_rule)
        assert difference["difference"] == pytest.approx(-0.35)
        assert difference["interval"] == pytest.approx([-2 / 3, -1 / 6])
        assert "verdict" not in difference
        sensitivity = code_gate["baseline_sensitivity"]
        assert sensitivity["verdict"] == analysis.SENSITIVITY_SENSITIVE  # recall_1 - recall_2 is 0.5 on every resample
        assert sensitivity["interval_lower_limit"] == pytest.approx(0.5)
        assert sensitivity["interval_upper_limit"] == pytest.approx(0.5)
        assert sensitivity["delta"] == analysis.DELTA

    def test_counts_the_defects_dropped_from_recall_and_from_precision(self, tmp_path: Path) -> None:
        """d3 has completed runs in one arm only, so recall drops it. d4 keeps its recall labels but
        has no precision-judge run, so precision drops it."""
        args = self._write_table(tmp_path, defect_ids=("d1",))
        defects.save_confirmed_defects(
            Path(args.defects_path), [_confirmed_single_defect(d) for d in ("d1", "d3", "d4")],
        )
        extra_reviewer = [
            _run_record("d3", arms_mod.ARM_CURRENT_RULE, "h1", "Leaks a connection on error."),
            _run_record("d3", arms_mod.ARM_FUNCTION_CONTEXT, "h2", "Nothing to report.", status="missing"),
            _run_record("d4", arms_mod.ARM_CURRENT_RULE, "f1", "Null pointer dereference."),
            _run_record("d4", arms_mod.ARM_FUNCTION_CONTEXT, "g1", "Null pointer dereference."),
        ]
        extra_judge = [
            _run_record("d3", JUDGE_ARM_RECALL, "d3-recall", 'h1: FOUND -- "Leaks a connection"'),
            _run_record("d4", JUDGE_ARM_RECALL, "d4-recall", 'f1: FOUND -- "Null pointer"\ng1: FOUND -- "Null pointer"'),
        ]
        runner.append_run_records(Path(args.reviewer_records_path), extra_reviewer)
        runner.append_run_records(Path(args.judge_records_path), extra_judge)

        assert run_review_bench.cmd_analyze(args) == 0

        report = json.loads(Path(args.out).read_text())
        assert report["kept_defect_ids"] == ["d1", "d4"]
        assert report["precision_kept_defect_ids"] == ["d1"]
        assert report["gates"]["code"]["effective_n"] == 2
        assert report["gates"]["code"]["precision_effective_n"] == 1
        assert report["dropped_defects_recall"] == 1
        assert report["dropped_defects_precision"] == 1

    def test_a_confirmed_defect_with_no_recall_label_counts_as_dropped_and_the_report_names_the_confirmed_total(
        self, tmp_path: Path,
    ) -> None:
        """d5 has no reviewer run and no judge answer, so the recall-label table never mentions it. The
        drop count still derives against the confirmed set."""
        args = self._write_table(tmp_path, defect_ids=("d1",))
        defects.save_confirmed_defects(Path(args.defects_path), [_confirmed_single_defect(d) for d in ("d1", "d5")])

        assert run_review_bench.cmd_analyze(args) == 0

        report = json.loads(Path(args.out).read_text())
        assert report["confirmed_defects"] == 2
        assert report["kept_defect_ids"] == ["d1"]
        assert report["dropped_defects_recall"] == 1

    def test_no_kept_defect_exits_2_with_a_message_and_writes_no_report(self, tmp_path: Path, capsys) -> None:
        args = self._write_table(tmp_path, defect_ids=("d1",))
        all_missing_path = tmp_path / "all-missing.jsonl"
        runner.append_run_records(all_missing_path, [
            _run_record("d1", arm, f"missing-{arm}", "", status="missing")
            for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT)
        ])
        args.reviewer_records_path = str(all_missing_path)

        assert run_review_bench.cmd_analyze(args) == 2

        stderr = capsys.readouterr().err
        assert "no defect is kept for recall" in stderr
        assert "dropped 1 confirmed defect(s) from recall" in stderr
        assert not Path(args.out).exists()

    def test_the_pooled_precision_fields_are_null_when_no_precision_judge_ran(self, tmp_path: Path) -> None:
        args = self._write_table(tmp_path, with_precision_judges=False)

        assert run_review_bench.cmd_analyze(args) == 0

        report = json.loads(Path(args.out).read_text())
        code_gate = report["gates"]["code"]
        for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
            assert code_gate["pooled_precision_per_arm"][arm]["precision"] is None
            assert code_gate["pooled_precision_per_arm"][arm]["interval"] is None
        assert code_gate["precision_difference"]["difference"] is None
        assert code_gate["precision_difference"]["interval"] is None
        assert code_gate["precision_effective_n"] == 0
        assert report["dropped_defects_precision"] == 2

    def test_the_report_has_no_key_containing_cost_or_usd_and_stderr_carries_both_cost_lines(
        self, tmp_path: Path, capsys,
    ) -> None:
        """Arm 1 has two priced runs (1.0 and 2.5) and two unpriced. Arm 2 has one priced run (0.75) and
        three unpriced. Each recall judge run costs 4.0, and no precision judge run is priced."""
        args = self._write_table(
            tmp_path, cost_by_run={"a1": 1.0, "a2": 2.5, "b1": 0.75}, recall_judge_cost_usd=4.0,
        )

        assert run_review_bench.cmd_analyze(args) == 0

        report_keys = _keys_at_every_depth(json.loads(Path(args.out).read_text()))
        assert report_keys
        assert not [key for key in report_keys if "cost" in key.lower() or "usd" in key.lower()]
        stderr_lines = capsys.readouterr().err.splitlines()
        cost_per_arm_line = next(line for line in stderr_lines if line.startswith("analyze: cost per arm ="))
        assert f"'{arms_mod.ARM_CURRENT_RULE}': {{'total_cost_usd': 3.5, 'runs_priced': 2, 'runs': 4}}" in cost_per_arm_line
        assert (
            f"'{arms_mod.ARM_FUNCTION_CONTEXT}': {{'total_cost_usd': 0.75, 'runs_priced': 1, 'runs': 4}}"
            in cost_per_arm_line
        )
        cost_per_judge_line = next(line for line in stderr_lines if line.startswith("analyze: cost per judge kind ="))
        assert f"'{JUDGE_ARM_RECALL}': {{'total_cost_usd': 8.0, 'runs_priced': 2, 'runs': 2}}" in cost_per_judge_line
        assert f"'{JUDGE_ARM_PRECISION}': {{'total_cost_usd': 0.0, 'runs_priced': 0, 'runs': 2}}" in cost_per_judge_line


class TestCmdAnalyzePerGateFigures:
    """Each gate's figures are computed over that gate's defects alone, never over the pooled set. K = 1, so
    a run's detection rate is 0 or 1. Defects (fixture in parentheses):
      code gate:      c1 (F1), c2 (F1), c3 (F2)
      markdown gate:  m1 (F3), m2 (F4)
      secondary:      s1 (F5), a non-markdown `pr-comment` defect
    Recall, arm 1 / arm 2: code 2/3 and 0, markdown 0 and 1/2, secondary 1 and 1, pooled 1/2 and 1/3.
    Pooled precision, arm 1 / arm 2: code 2/4 and 1/3, markdown 2/2 and 0/3, secondary 1/1 and 0/1.
    With two fixtures, a resample draws (F, F), (F, F'), or (F', F') with probability 1/4, 1/2, 1/4, so each
    interval's two ends are its two same-fixture resample values. The code gate's F1 holds two defects."""

    _CONFIRMED = [
        _confirmed_single_defect("c1", fixture="F1"), _confirmed_single_defect("c2", fixture="F1"),
        _confirmed_single_defect("c3", source="review-round", fixture="F2"),
        _confirmed_single_defect("m1", source="review-round", file_is_markdown=True, fixture="F3"),
        _confirmed_single_defect("m2", source="pr-comment", file_is_markdown=True, fixture="F4"),
        _confirmed_single_defect("s1", source="pr-comment", fixture="F5"),
    ]
    _TABLE = {
        "c1": {_ARM_1: (True, ("VALID", "INVALID")), _ARM_2: (False, ("INVALID",))},
        "c2": {_ARM_1: (True, ("VALID",)), _ARM_2: (False, ("INVALID",))},
        "c3": {_ARM_1: (False, ("INVALID",)), _ARM_2: (False, ("VALID",))},
        "m1": {_ARM_1: (False, ("VALID",)), _ARM_2: (True, ("INVALID",))},
        "m2": {_ARM_1: (False, ("VALID",)), _ARM_2: (False, ("INVALID", "INVALID"))},
        "s1": {_ARM_1: (True, ("VALID",)), _ARM_2: (True, ("INVALID",))},
    }

    def _report(self, tmp_path: Path, monkeypatch, *, later_arm: bool) -> dict:
        files = _frozen_files(tmp_path, monkeypatch, confirmed=self._CONFIRMED)
        reviewer_path, judge_path = _judged_table(tmp_path, self._TABLE)
        out_path = tmp_path / "report.json"
        argv = (
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)) if later_arm
            else [
                "analyze", "--defects-path", str(files["defects"]), "--reviewer-records-path", str(reviewer_path),
                "--judge-records-path", str(judge_path), "--k", "1", "--out", str(out_path),
                "--arms-root", str(files["arms"]),
            ]
        )
        assert run_review_bench.main(argv) == 0
        return json.loads(out_path.read_text())

    @staticmethod
    def _assert_gate_figures_are_the_gates_own(report: dict) -> None:
        code, markdown = report["gates"]["code"], report["gates"]["markdown"]
        assert (code["effective_n"], code["fixture_count"], code["largest_cluster_size"]) == (3, 2, 2)
        assert (markdown["effective_n"], markdown["fixture_count"], markdown["largest_cluster_size"]) == (2, 2, 1)
        assert (code["precision_effective_n"], markdown["precision_effective_n"]) == (3, 2)
        assert code["recall_per_arm"][_ARM_1]["recall"] == pytest.approx(2 / 3)
        assert code["recall_per_arm"][_ARM_1]["interval"] == pytest.approx([0.0, 1.0])
        assert code["recall_per_arm"][_ARM_2]["recall"] == 0.0
        assert code["recall_difference"]["difference"] == pytest.approx(-2 / 3)
        assert code["recall_difference"]["interval"] == pytest.approx([-1.0, 0.0])
        assert code["pooled_precision_per_arm"][_ARM_1]["precision"] == pytest.approx(0.5)
        assert code["pooled_precision_per_arm"][_ARM_1]["interval"] == pytest.approx([0.0, 2 / 3])
        assert code["pooled_precision_per_arm"][_ARM_2]["precision"] == pytest.approx(1 / 3)
        assert code["precision_difference"]["difference"] == pytest.approx(-1 / 6)
        assert code["precision_difference"]["interval"] == pytest.approx([-2 / 3, 1.0])
        assert markdown["recall_per_arm"][_ARM_1]["recall"] == 0.0
        assert markdown["recall_per_arm"][_ARM_2]["recall"] == pytest.approx(0.5)
        assert markdown["recall_difference"]["difference"] == pytest.approx(0.5)
        assert markdown["recall_difference"]["interval"] == pytest.approx([0.0, 1.0])
        assert markdown["pooled_precision_per_arm"][_ARM_1]["precision"] == 1.0
        assert markdown["pooled_precision_per_arm"][_ARM_2]["precision"] == 0.0
        assert markdown["precision_difference"]["difference"] == -1.0
        assert markdown["precision_difference"]["interval"] == pytest.approx([-1.0, -1.0])

    def test_baseline_mode_reports_each_gates_own_figures_and_sensitivity_verdict(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        report = self._report(tmp_path, monkeypatch, later_arm=False)

        self._assert_gate_figures_are_the_gates_own(report)
        code_sensitivity = report["gates"]["code"]["baseline_sensitivity"]
        assert code_sensitivity["verdict"] == analysis.SENSITIVITY_NOT_SENSITIVE
        assert code_sensitivity["interval_lower_limit"] == pytest.approx(0.0)
        assert code_sensitivity["interval_upper_limit"] == pytest.approx(1.0)
        markdown_sensitivity = report["gates"]["markdown"]["baseline_sensitivity"]
        assert markdown_sensitivity["verdict"] == analysis.SENSITIVITY_NOT_SENSITIVE
        assert markdown_sensitivity["interval_lower_limit"] == pytest.approx(-1.0)
        assert "certification" not in report

    def test_later_arm_mode_reports_each_gates_own_figures_and_verdicts(self, tmp_path: Path, monkeypatch) -> None:
        report = self._report(tmp_path, monkeypatch, later_arm=True)

        self._assert_gate_figures_are_the_gates_own(report)
        code, markdown = report["gates"]["code"], report["gates"]["markdown"]
        assert code["recall_noninferiority"]["verdict"] == analysis.NONINFERIORITY_FAIL  # lower limit -1.0
        assert markdown["recall_noninferiority"]["verdict"] == analysis.NONINFERIORITY_PASS  # lower limit 0.0
        assert code["precision_noninferiority"]["verdict"] == analysis.NONINFERIORITY_FAIL  # lower limit -2/3
        assert markdown["precision_noninferiority"]["verdict"] == analysis.NONINFERIORITY_FAIL  # lower limit -1.0
        assert report["certification"] == analysis.CERTIFICATION_NOT_CERTIFIED

    @pytest.mark.parametrize("later_arm", [False, True])
    def test_each_gates_count_line_reports_that_gates_defects_fixtures_and_largest_fixture(
        self, tmp_path: Path, monkeypatch, capsys, later_arm: bool,
    ) -> None:
        self._report(tmp_path, monkeypatch, later_arm=later_arm)

        stderr_lines = capsys.readouterr().err.splitlines()
        assert "analyze: code: 3 kept defect(s) over 2 fixture(s), largest fixture holds 2" in stderr_lines
        assert "analyze: markdown: 2 kept defect(s) over 2 fixture(s), largest fixture holds 1" in stderr_lines

    @pytest.mark.parametrize("later_arm", [False, True])
    def test_per_source_and_secondary_strata_carry_figures_and_no_verdict_key(
        self, tmp_path: Path, monkeypatch, later_arm: bool,
    ) -> None:
        report = self._report(tmp_path, monkeypatch, later_arm=later_arm)

        assert sorted(report["per_source"]) == sorted(defects.KNOWN_SOURCES)
        szz, review_round = report["per_source"]["szz"], report["per_source"]["review-round"]
        assert (szz["effective_n"], szz["fixture_count"]) == (2, 1)
        assert szz["recall_per_arm"][_ARM_1] == {"recall": 1.0, "interval": None}
        assert (review_round["effective_n"], review_round["fixture_count"]) == (2, 2)
        assert review_round["recall_per_arm"][_ARM_2]["recall"] == pytest.approx(0.5)
        assert review_round["recall_per_arm"][_ARM_2]["interval"] == pytest.approx([0.0, 1.0])
        secondary = report["secondary_stratum"]
        assert (secondary["effective_n"], secondary["fixture_count"]) == (1, 1)
        assert secondary["recall_difference"]["difference"] == 0.0
        assert secondary["recall_difference"]["interval"] is None
        assert secondary["pooled_precision_per_arm"][_ARM_2]["precision"] == 0.0
        stratum_keys = _keys_at_every_depth([report["per_source"], report["secondary_stratum"]])
        assert not [
            key for key in stratum_keys
            if any(word in key.lower() for word in ("verdict", "outcome", "noninferiority", "certif"))
        ]

    def test_an_empty_gate_reports_null_figures_and_a_null_baseline_verdict_without_raising(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=("d1", "d2"))
        reviewer_path, judge_path = _judged_records(tmp_path, defect_ids=("d1", "d2"))
        out_path = tmp_path / "report.json"

        exit_code = run_review_bench.main([
            "analyze", "--defects-path", str(files["defects"]), "--reviewer-records-path", str(reviewer_path),
            "--judge-records-path", str(judge_path), "--k", "1", "--out", str(out_path),
            "--arms-root", str(files["arms"]),
        ])

        assert exit_code == 0
        markdown = json.loads(out_path.read_text())["gates"]["markdown"]
        assert (markdown["effective_n"], markdown["fixture_count"], markdown["largest_cluster_size"]) == (0, 0, 0)
        assert markdown["effective_n_meets_n_min"] is False
        assert markdown["recall_per_arm"][_ARM_1] == {"recall": None, "interval": None}
        assert markdown["pooled_precision_per_arm"][_ARM_1]["precision"] is None
        assert markdown["precision_difference"]["difference"] is None
        assert markdown["baseline_sensitivity"]["verdict"] is None

    def test_an_empty_gate_in_a_later_arm_is_inconclusive_with_null_verdicts_without_raising(
        self, tmp_path: Path, monkeypatch, patch_n_min,
    ) -> None:
        patch_n_min(2)
        confirmed = _two_gate_defects(markdown_count=0)
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        files["baseline_report"].write_text(json.dumps(_baseline_report(
            json.loads(files["conditions"].read_text()), gate_verdicts={"markdown": None}, fixture_counts={"markdown": 0},
        )))
        reviewer_path, judge_path = _judged_table(
            tmp_path, {defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in confirmed},
        )
        out_path = tmp_path / "report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)),
        )

        assert exit_code == 0
        report = json.loads(out_path.read_text())
        markdown = report["gates"]["markdown"]
        assert markdown["outcome"] == analysis.GATE_OUTCOME_INCONCLUSIVE
        assert (markdown["recall_noninferiority"]["verdict"], markdown["precision_noninferiority"]["verdict"]) == (None, None)
        assert report["gates"]["code"]["outcome"] == analysis.GATE_OUTCOME_PASS
        assert report["certification"] == analysis.CERTIFICATION_INCONCLUSIVE


class TestParserRejectsFourNMinFlagSpellings:
    """A tripwire on four flag spellings, not a proof that no flag reaches N_min(K); the pin that N_min(K) is
    computed from K alone is the unpatched `analysis.n_min(2)` report-value test."""

    _REQUIRED_ARGV = ["analyze", "--reviewer-records-path", "r.jsonl", "--judge-records-path", "j.jsonl", "--k", "1"]

    def test_the_parser_rejects_each_of_the_four_n_min_flag_spellings(self) -> None:
        parser = run_review_bench.build_parser()
        parser.parse_args(self._REQUIRED_ARGV)  # positive control: the required argv alone parses

        for flag in ("--n-min", "--n-min-override", "--nmin", "--override-n-min"):
            with pytest.raises(SystemExit) as exit_info:
                parser.parse_args([*self._REQUIRED_ARGV, flag, "1"])
            assert exit_info.value.code == 2


class TestCmdAnalyzeCliFlags:
    def test_cli_flags_reach_cmd_analyze_via_build_parser(self, tmp_path: Path) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])

        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "Leaks a connection on error."),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "Nothing to report."),
        ])
        judge_records_path = tmp_path / "judge.jsonl"
        runner.append_run_records(judge_records_path, [
            _run_record("d1", JUDGE_ARM_RECALL, "j-recall", 'run-a: FOUND -- "Leaks a connection"\nrun-b: NOT_FOUND'),
            _run_record(
                "d1", JUDGE_ARM_PRECISION, "j-precision", '### Run run-a\n1. VALID -- "Leaks a connection"\n### Run run-b\n',
            ),
        ])

        out_path = tmp_path / "report.json"
        args = run_review_bench.build_parser().parse_args([
            "analyze", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_records_path),
            "--judge-records-path", str(judge_records_path), "--k", "1", "--out", str(out_path),
            "--arms-root", str(_arms_root_with_both_arm_snapshots(tmp_path)),
        ])

        exit_code = run_review_bench.cmd_analyze(args)

        assert exit_code == 0
        report = json.loads(out_path.read_text())
        # kept_defect_ids requires joining reviewer_records against
        # judge_records by arm (JUDGE_ARM_RECALL) -- a swapped
        # --reviewer-records-path/--judge-records-path destination leaves
        # that join empty. It also requires args.k's completed-runs
        # threshold (1 completed run >= k/2) to gate d1 in rather than out,
        # so a --k not reaching that comparison as an int fails the test too.
        assert report["kept_defect_ids"] == ["d1"]


def _arms_root_with_both_arm_snapshots(tmp_path: Path) -> Path:
    arms_root = tmp_path / "arms"
    for arm in (arms_mod.ARM_CURRENT_RULE, arms_mod.ARM_FUNCTION_CONTEXT):
        (arms_root / arm).mkdir(parents=True)
        (arms_root / arm / "bench-staff-backend-engineer.md").write_text(f"{arm} agent body\n")
    return arms_root


def _frozen_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, defect_ids=("d1",), k: int = 1, campaign_seed: int = 0,
    confirmed: list[ConfirmedDefect] | None = None,
) -> dict[str, Path]:
    """A defects file, both arm snapshot dirs, private judge-agent copies, a
    conditions.json frozen over them, and a baseline report that reads sensitive in both gates,
    for a test to edit one file at a time. `confirmed` replaces the defects built from `defect_ids`."""
    from review_bench import adjudicate

    defects_path = tmp_path / "defects.json"
    defects.save_confirmed_defects(
        defects_path, confirmed or [_confirmed_single_defect(defect_id) for defect_id in defect_ids],
    )
    arms_root = _arms_root_with_both_arm_snapshots(tmp_path)
    for attribute, name in (
        ("RECALL_JUDGE_AGENT_FILE", "bench-judge-recall.md"), ("PRECISION_JUDGE_AGENT_FILE", "bench-judge-precision.md"),
    ):
        judge_file = tmp_path / name
        judge_file.write_text(f"{name} rubric\n")
        monkeypatch.setattr(adjudicate, attribute, judge_file)

    conditions_path = tmp_path / "conditions.json"
    conditions = {
        **analysis.compute_frozen_fields(defects_path, arms_root), "k": k, "campaign_seed": campaign_seed,
        "environment": {
            "cli_version": _STUBBED_ENVIRONMENT.cli_version,
            "ambient_config_commit": _STUBBED_ENVIRONMENT.ambient_config_commit,
        },
    }
    conditions_path.write_text(json.dumps(conditions))
    baseline_report_path = tmp_path / "baseline-report.json"
    baseline_report_path.write_text(json.dumps(_baseline_report(conditions)))
    return {
        "defects": defects_path, "arms": arms_root, "conditions": conditions_path,
        "baseline_report": baseline_report_path,
        "recall_judge": tmp_path / "bench-judge-recall.md", "precision_judge": tmp_path / "bench-judge-precision.md",
    }


def _baseline_report(conditions: dict, *, gate_verdicts: dict | None = None, fixture_counts: dict | None = None) -> dict:
    """The fields of a baseline-mode report that a later arm reads, with the freeze identity of `conditions`.
    Each gate reads sensitive over 130 fixtures unless overridden."""
    verdicts = {"code": analysis.SENSITIVITY_SENSITIVE, "markdown": analysis.SENSITIVITY_SENSITIVE, **(gate_verdicts or {})}
    counts = {"code": 130, "markdown": 130, **(fixture_counts or {})}
    return {
        "freeze_identity": {
            "harness_closure_hash": conditions["harness_closure_hash"],
            **{field: conditions[field] for field in analysis._FROZEN_DIGEST_FIELDS},
            "k": conditions["k"],
            "environment": dict(conditions["environment"]),
        },
        "gates": {
            gate: {"baseline_sensitivity": {"verdict": verdicts[gate]}, "fixture_count": counts[gate]}
            for gate in ("code", "markdown")
        },
    }


def _judged_records(tmp_path: Path, *, defect_ids=("d1",)) -> tuple[Path, Path]:
    reviewer_records_path = tmp_path / "reviewer.jsonl"
    judge_records_path = tmp_path / "judge.jsonl"
    reviewer_records, judge_records = [], []
    for defect_id in defect_ids:
        reviewer_records += [
            _run_record(defect_id, arms_mod.ARM_CURRENT_RULE, f"{defect_id}-a", "Leaks a connection on error."),
            _run_record(defect_id, arms_mod.ARM_FUNCTION_CONTEXT, f"{defect_id}-b", "Nothing to report."),
        ]
        judge_records += [
            _run_record(
                defect_id, JUDGE_ARM_RECALL, f"{defect_id}-recall",
                f'{defect_id}-a: FOUND -- "Leaks a connection"\n{defect_id}-b: NOT_FOUND',
            ),
            _run_record(
                defect_id, JUDGE_ARM_PRECISION, f"{defect_id}-precision",
                f'### Run {defect_id}-a\n1. VALID -- "Leaks a connection"\n### Run {defect_id}-b\n',
            ),
        ]
    runner.append_run_records(reviewer_records_path, reviewer_records)
    runner.append_run_records(judge_records_path, judge_records)
    return reviewer_records_path, judge_records_path


def _analyze_argv(files: dict[str, Path], reviewer_path: Path, judge_path: Path, *extra: str) -> list[str]:
    return [
        "analyze", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
        "--reviewer-records-path", str(reviewer_path), "--judge-records-path", str(judge_path),
        "--baseline-conditions-path", str(files["conditions"]),
        "--baseline-report-path", str(files["baseline_report"]), *extra,
    ]


# Each gate in the later-arm data-path tests holds at most two fixtures, so one meets this bar.
_N_MIN_NO_GATE_IS_SHORT = 1


@pytest.fixture
def patch_n_min(monkeypatch):
    """Returns a setter that replaces N_min(K) with a fixed value, so a few fixtures can build a gate that meets it."""

    def set_n_min(value: int) -> None:
        monkeypatch.setattr(analysis, "n_min", lambda k: value)

    return set_n_min


_REJECTED_DESCRIPTION_CHARACTERS = ["\x1b", "\ufe0f", "\u3164", "\u2800", "\u2028", "\ue000"]


def _edit_first_description(defects_path: Path, description: str) -> None:
    """Hand-edits `defects.json`, past every writer that validates."""
    records = json.loads(defects_path.read_text())
    records[0]["description"] = description
    defects_path.write_text(json.dumps(records))


@pytest.mark.usefixtures("stubbed_environment")
class TestAHandEditedDescriptionWithARejectedCharacterReachesNoSubcommand:
    """`ConfirmedDefect` validates its description when a record loads, so `freeze`, `judge`, and `run`
    refuse a defects file edited past `confirm`."""

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_freeze_raises_naming_the_code_point_and_writes_no_conditions(
        self, tmp_path: Path, monkeypatch, character: str,
    ) -> None:
        args = _prepare_freeze(tmp_path, monkeypatch)
        _edit_first_description(Path(args.defects_path), f"x {character} y")

        with pytest.raises(ValueError, match=rf"U\+{ord(character):04X}"):
            run_review_bench.cmd_freeze(args)

        assert not Path(args.out).exists()

    @pytest.mark.parametrize("subcommand", ["judge", "run"])
    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_judge_and_run_raise_before_any_dispatch(
        self, tmp_path: Path, monkeypatch, subcommand: str, character: str,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, _judge_path = _judged_records(tmp_path)
        _edit_first_description(files["defects"], f"x {character} y")
        argv = [
            subcommand, "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--conditions-path", str(files["conditions"]),
        ]
        if subcommand == "judge":
            argv += ["--reviewer-records-path", str(reviewer_path)]

        with pytest.raises(ValueError, match=rf"U\+{ord(character):04X}"):
            run_review_bench.main(argv)


class TestAnalyzeRefusesRecordsFromTwoEnvironments:
    """`analyze` backstops the per-block environment checks: records from
    blocks run under different ambient config commits are not one comparable
    campaign."""

    def test_records_from_two_blocks_under_different_ambient_config_commits_exit_2_naming_both(
        self, tmp_path: Path, capsys,
    ) -> None:
        import dataclasses

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect("d1"), _confirmed_single_defect("d2")])
        reviewer_path, judge_path = _judged_records(tmp_path, defect_ids=("d1", "d2"))
        reviewer_records = [
            dataclasses.replace(record, ambient_config_commit="cafebabe") if record.defect_id == "d2" else record
            for record in runner.read_run_records(reviewer_path)
        ]
        reviewer_path.unlink()
        runner.append_run_records(reviewer_path, reviewer_records)

        exit_code = run_review_bench.main([
            "analyze", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_path),
            "--judge-records-path", str(judge_path), "--k", "1", "--out", str(tmp_path / "report.json"),
        ])

        assert exit_code == 2
        error_text = capsys.readouterr().err
        assert "more than one environment" in error_text
        assert "'2.0.0'@'deadbeef': ['d1']" in error_text
        assert "'2.0.0'@'cafebabe': ['d2']" in error_text
        assert not (tmp_path / "report.json").exists()


@pytest.mark.usefixtures("stubbed_environment")
class TestBaselineAnalyzeRefusesRecordsNamingAnUnconfirmedDefect:
    """Baseline-mode `analyze` exits 2 naming a defect the records carry and
    defects.json lacks, instead of crashing on the missing key."""

    @pytest.mark.parametrize("with_out", [True, False], ids=["with-out", "without-out"])
    @pytest.mark.parametrize(
        ("record_file", "named_kind"), [("reviewer", "reviewer"), ("judge", "judge"), ("both", "reviewer")],
    )
    def test_records_naming_a_defect_absent_from_defects_json_exit_2_naming_it_and_the_record_kind(
        self, tmp_path: Path, capsys, record_file: str, named_kind: str, with_out: bool,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect("d1")])
        reviewer_path, judge_path = _judged_records(tmp_path, defect_ids=("d1", "d2"))
        if record_file == "judge":
            kept_reviewer_records = [r for r in runner.read_run_records(reviewer_path) if r.defect_id == "d1"]
            reviewer_path.unlink()
            runner.append_run_records(reviewer_path, kept_reviewer_records)
        if record_file == "reviewer":
            kept_judge_records = [r for r in runner.read_run_records(judge_path) if r.defect_id == "d1"]
            judge_path.unlink()
            runner.append_run_records(judge_path, kept_judge_records)
        out_args = ["--out", str(tmp_path / "report.json")] if with_out else []

        exit_code = run_review_bench.main([
            "analyze", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_path),
            "--judge-records-path", str(judge_path), "--k", "1", *out_args,
        ])

        assert exit_code == 2
        error_text = capsys.readouterr().err
        assert f"{named_kind} records name defects absent from defects.json: ['d2']" in error_text
        assert not (tmp_path / "report.json").exists()


class TestAnalyzeRecomputesFrozenConditions:
    """A later arm's `analyze` must recompute the arm, judge-agent, and
    defects file hashes `freeze` recorded, so an edit after the freeze exits 2
    naming the field."""

    def test_unchanged_files_and_matching_k_and_defects_analyze_normally(self, tmp_path: Path, monkeypatch) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_records(tmp_path)

        assert run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1")) == 0

    @pytest.mark.parametrize(("edited", "expected_field"), [
        pytest.param("arm", f"arm_dir_hashes[{arms_mod.ARM_CURRENT_RULE}]", id="arm-body"),
        pytest.param("precision_judge", "judge_agent_hashes[bench-judge-precision]", id="judge-rubric"),
        pytest.param("recall_judge", "judge_agent_hashes[bench-judge-recall]", id="recall-judge-rubric"),
        pytest.param("defects", "defects_json_hash", id="defects-json"),
    ])
    def test_a_file_edited_after_the_freeze_exits_2_naming_the_field(
        self, tmp_path: Path, monkeypatch, capsys, edited: str, expected_field: str,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_records(tmp_path)
        if edited == "arm":
            (files["arms"] / arms_mod.ARM_CURRENT_RULE / "bench-staff-backend-engineer.md").write_text("edited\n")
        elif edited == "defects":
            defects.save_confirmed_defects(files["defects"], [
                _confirmed_single_defect("d1"), _confirmed_single_defect("d2"),
            ])
        else:
            files[edited].write_text("edited rubric\n")

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        assert expected_field in capsys.readouterr().err

    def test_the_later_arm_report_has_no_key_containing_cost_or_usd_and_stderr_carries_the_cost_lines(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import dataclasses

        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_records(tmp_path)
        priced_records = [dataclasses.replace(record, total_cost_usd=2.5) for record in runner.read_run_records(reviewer_path)]
        reviewer_path.unlink()
        runner.append_run_records(reviewer_path, priced_records)
        report_path = tmp_path / "later-arm-report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(report_path)),
        )

        assert exit_code == 0
        report_keys = _keys_at_every_depth(json.loads(report_path.read_text()))
        assert report_keys
        assert not [key for key in report_keys if "cost" in key.lower() or "usd" in key.lower()]
        stderr = capsys.readouterr().err
        assert f"'{arms_mod.ARM_CURRENT_RULE}': {{'total_cost_usd': 2.5, 'runs_priced': 1, 'runs': 1}}" in stderr

    def test_a_k_other_than_the_frozen_k_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        files = _frozen_files(tmp_path, monkeypatch, k=10)
        reviewer_path, judge_path = _judged_records(tmp_path)

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "--k 1 differs from the frozen k 10 -- pass --k 10" in stderr
        assert "rerun all arms" not in stderr

    def test_a_record_for_a_defect_that_is_not_frozen_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=("d1",))
        reviewer_path, judge_path = _judged_records(tmp_path, defect_ids=("d1", "d9"))

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        assert "unfrozen defects: ['d9']" in capsys.readouterr().err

    def test_a_frozen_defect_with_no_record_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=("d1", "d2"))
        reviewer_path, judge_path = _judged_records(tmp_path, defect_ids=("d1",))

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        assert "no records: ['d2']" in capsys.readouterr().err


def _judged_table(
    tmp_path: Path, table: dict[str, dict[str, tuple[bool, tuple[str, ...]]]], *, subdir: str = "",
    defects_without_precision_answer: tuple[str, ...] = (),
) -> tuple[Path, Path]:
    """Reviewer and judge records for one run per arm and defect (K = 1). `table` maps a defect ID to
    {arm: (found, finding labels)}, a label being "VALID" or "INVALID". The arm's run holds one finding per
    label, `found` is the recall judge's FOUND for that run, and a found run needs at least one finding."""
    directory = tmp_path / subdir if subdir else tmp_path
    directory.mkdir(exist_ok=True)
    reviewer_path, judge_path = directory / "reviewer.jsonl", directory / "judge.jsonl"
    reviewer_records, judge_records = [], []
    for defect_id, per_arm in table.items():
        recall_lines, precision_sections = [], []
        for arm, (found, labels) in per_arm.items():
            run_id = f"{defect_id}-{'one' if arm == _ARM_1 else 'two'}"
            findings = " ".join(f"Finding {number} text." for number in range(1, len(labels) + 1)) or "Nothing to report."
            reviewer_records.append(_run_record(defect_id, arm, run_id, findings))
            recall_lines.append(f'{run_id}: FOUND -- "Finding 1"' if found else f"{run_id}: NOT_FOUND")
            precision_sections.append(
                f"### Run {run_id}\n"
                + "".join(f'{number}. {label} -- "Finding {number}"\n' for number, label in enumerate(labels, start=1))
            )
        judge_records.append(_run_record(defect_id, JUDGE_ARM_RECALL, f"{defect_id}-recall", "\n".join(recall_lines)))
        if defect_id not in defects_without_precision_answer:
            judge_records.append(
                _run_record(defect_id, JUDGE_ARM_PRECISION, f"{defect_id}-precision", "".join(precision_sections)),
            )
    runner.append_run_records(reviewer_path, reviewer_records)
    runner.append_run_records(judge_path, judge_records)
    return reviewer_path, judge_path


_BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING = {_ARM_1: (True, ("VALID",)), _ARM_2: (True, ("VALID",))}
_ONLY_ARM_1_FINDS = {_ARM_1: (True, ("VALID",)), _ARM_2: (False, ("VALID",))}


def _two_gate_defects(*, code_count: int = 2, markdown_count: int = 2) -> list[ConfirmedDefect]:
    """Defects on fixtures of their own: `code_count` in the code gate, `markdown_count` in the markdown gate."""
    return [
        *(_confirmed_single_defect(f"c{i}") for i in range(1, code_count + 1)),
        *(
            _confirmed_single_defect(f"m{i}", source="review-round", file_is_markdown=True)
            for i in range(1, markdown_count + 1)
        ),
    ]


class TestAnalyzeLaterArmVerdicts:
    """`analyze --baseline-conditions-path` certifies or rejects a later arm. Each gate's verdicts
    are pinned here on constructed records, as are the refusals, the kept-set gating and the
    missing-precision refusal. The precision FAIL verdict is pinned by the unit tests alone."""

    def test_a_later_arm_matching_the_baseline_below_n_min_is_inconclusive(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch, confirmed=_two_gate_defects())
        reviewer_path, judge_path = _judged_table(
            tmp_path, {defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in _two_gate_defects()},
        )

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        stderr = capsys.readouterr().err
        assert exit_code == 0
        for gate in ("code", "markdown"):
            assert f"analyze: {gate} gate recall non-inferiority = {analysis.NONINFERIORITY_PASS}" in stderr
            assert f"analyze: {gate} gate precision non-inferiority = {analysis.NONINFERIORITY_PASS}" in stderr
        assert f"analyze: certification = {analysis.CERTIFICATION_INCONCLUSIVE}" in stderr

    @pytest.mark.parametrize(
        ("code_count", "markdown_count", "defects_without_precision_answer", "patched_n_min", "expected_certification"),
        [
            pytest.param(2, 2, (), 2, analysis.CERTIFICATION_CERTIFIED, id="both-gates-fixture-count-equals-n-min"),
            pytest.param(2, 2, (), 3, analysis.CERTIFICATION_INCONCLUSIVE, id="both-gates-fixture-count-one-below-n-min"),
            pytest.param(
                3, 2, ("c3",), 3, analysis.CERTIFICATION_INCONCLUSIVE, id="recall-kept-meets-n-min-but-precision-kept-does-not",
            ),
            pytest.param(3, 2, ("c3",), 2, analysis.CERTIFICATION_CERTIFIED, id="precision-kept-fixture-count-equals-n-min"),
            pytest.param(
                2, 2, ("c2",), 1, analysis.CERTIFICATION_INCONCLUSIVE, id="one-precision-kept-fixture-has-no-interval",
            ),
        ],
    )
    def test_the_n_min_bar_counts_each_gates_fixtures_and_binds_the_smaller_kept_set(
        self, tmp_path: Path, monkeypatch, capsys, patch_n_min, code_count: int, markdown_count: int,
        defects_without_precision_answer: tuple[str, ...], patched_n_min: int, expected_certification: str,
    ) -> None:
        patch_n_min(patched_n_min)
        confirmed = _two_gate_defects(code_count=code_count, markdown_count=markdown_count)
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        reviewer_path, judge_path = _judged_table(
            tmp_path, {defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in confirmed},
            defects_without_precision_answer=defects_without_precision_answer,
        )

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        stderr = capsys.readouterr().err
        assert exit_code == 0
        assert f"analyze: code gate recall non-inferiority = {analysis.NONINFERIORITY_PASS}" in stderr
        assert f"analyze: certification = {expected_certification}" in stderr

    def test_a_later_arm_that_misses_the_defects_the_baseline_found_in_one_gate_is_not_certified(
        self, tmp_path: Path, monkeypatch, capsys, patch_n_min,
    ) -> None:
        patch_n_min(2)
        confirmed = _two_gate_defects()
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        reviewer_path, judge_path = _judged_table(tmp_path, {
            **{defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in confirmed if defect.id.startswith("c")},
            **{defect.id: _ONLY_ARM_1_FINDS for defect in confirmed if defect.id.startswith("m")},
        })
        out_path = tmp_path / "later-arm-report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)),
        )

        stderr = capsys.readouterr().err
        assert exit_code == 0
        assert f"analyze: code gate recall non-inferiority = {analysis.NONINFERIORITY_PASS}" in stderr
        assert f"analyze: markdown gate recall non-inferiority = {analysis.NONINFERIORITY_FAIL}" in stderr
        assert f"analyze: certification = {analysis.CERTIFICATION_NOT_CERTIFIED}" in stderr
        report = json.loads(out_path.read_text())
        assert report["certification"] == analysis.CERTIFICATION_NOT_CERTIFIED
        assert (report["gates"]["code"]["outcome"], report["gates"]["markdown"]["outcome"]) == (
            analysis.GATE_OUTCOME_PASS, analysis.GATE_OUTCOME_FAIL,
        )

    def test_a_secondary_stratum_that_loses_decisively_leaves_a_later_arm_passing_both_gates_certified(
        self, tmp_path: Path, monkeypatch, patch_n_min,
    ) -> None:
        """Non-markdown `pr-comment` defects form the secondary stratum, which is reported and never gates."""
        patch_n_min(2)
        secondary = [_confirmed_single_defect(f"s{i}", source="pr-comment") for i in (1, 2)]
        confirmed = [*_two_gate_defects(), *secondary]
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        arm_1_alone_finds_validly = {_ARM_1: (True, ("VALID",)), _ARM_2: (False, ("INVALID",))}
        reviewer_path, judge_path = _judged_table(tmp_path, {
            **{defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in _two_gate_defects()},
            **{defect.id: arm_1_alone_finds_validly for defect in secondary},
        })
        out_path = tmp_path / "later-arm-report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)),
        )

        assert exit_code == 0
        report = json.loads(out_path.read_text())
        assert report["certification"] == analysis.CERTIFICATION_CERTIFIED
        assert (report["gates"]["code"]["outcome"], report["gates"]["markdown"]["outcome"]) == (
            analysis.GATE_OUTCOME_PASS, analysis.GATE_OUTCOME_PASS,
        )
        stratum = report["secondary_stratum"]
        assert stratum["recall_difference"]["difference"] == -1.0  # arm 2 finds neither secondary defect
        assert stratum["pooled_precision_per_arm"][_ARM_1]["precision"] == 1.0
        assert stratum["pooled_precision_per_arm"][_ARM_2]["precision"] == 0.0
        assert not [
            key for key in _keys_at_every_depth(stratum)
            if any(word in key.lower() for word in ("verdict", "outcome", "noninferiority", "certif"))
        ]

    def test_a_failing_gate_with_an_empty_other_gate_is_still_not_certified(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        confirmed = [_confirmed_single_defect("c1"), _confirmed_single_defect("c2")]
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        reviewer_path, judge_path = _judged_table(tmp_path, {defect.id: _ONLY_ARM_1_FINDS for defect in confirmed})

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        stderr = capsys.readouterr().err
        assert exit_code == 0
        assert f"analyze: code gate recall non-inferiority = {analysis.NONINFERIORITY_FAIL}" in stderr
        assert f"analyze: certification = {analysis.CERTIFICATION_NOT_CERTIFIED}" in stderr

    def test_a_later_arm_with_recall_but_no_precision_judge_answer_exits_2_without_a_traceback(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_table(
            tmp_path, {"d1": _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING}, defects_without_precision_answer=("d1",),
        )
        out_path = tmp_path / "report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)),
        )

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "no defect is kept for precision" in stderr
        assert not out_path.exists()

    def test_records_from_another_environment_than_the_baseline_exit_2(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        conditions = json.loads(files["conditions"].read_text())
        conditions["environment"]["cli_version"] = "9.9.9-other"
        files["conditions"].write_text(json.dumps(conditions))
        reviewer_path, judge_path = _judged_records(tmp_path)

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        assert "differs from baseline" in capsys.readouterr().err

    def test_a_harness_closure_that_differs_from_the_frozen_manifest_exits_2_naming_the_module(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        conditions = json.loads(files["conditions"].read_text())
        edited_module = sorted(conditions["harness_closure"])[0]
        conditions["harness_closure"][edited_module] = "0" * 64
        files["conditions"].write_text(json.dumps(conditions))
        reviewer_path, judge_path = _judged_records(tmp_path)

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        assert edited_module in capsys.readouterr().err


class TestAnalyzeLaterArmReadsThePerGateBaselineVerdicts:
    """Later-arm `analyze` reads each gate's sensitivity verdict from the baseline report. Arm X
    passes all four tests in every case here, and the patched N_min keeps no gate short, so the outcome
    comes from the baseline verdicts alone."""

    @pytest.fixture(autouse=True)
    def _no_gate_is_short(self, patch_n_min) -> None:
        patch_n_min(_N_MIN_NO_GATE_IS_SHORT)

    def _certification(
        self, tmp_path: Path, monkeypatch, *, gate_verdicts: dict | None = None,
        fixture_counts: dict | None = None, defect_counts: tuple[int, int] = (2, 2),
    ) -> str:
        confirmed = _two_gate_defects(code_count=defect_counts[0], markdown_count=defect_counts[1])
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        conditions = json.loads(files["conditions"].read_text())
        files["baseline_report"].write_text(
            json.dumps(_baseline_report(conditions, gate_verdicts=gate_verdicts, fixture_counts=fixture_counts)),
        )
        reviewer_path, judge_path = _judged_table(
            tmp_path, {defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in confirmed},
        )
        out_path = tmp_path / "later-arm-report.json"
        argv = _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path))
        assert run_review_bench.main(argv) == 0
        return json.loads(out_path.read_text())["certification"]

    def test_both_gates_sensitive_with_no_gate_short_certifies(self, tmp_path: Path, monkeypatch) -> None:
        assert self._certification(tmp_path, monkeypatch) == analysis.CERTIFICATION_CERTIFIED

    @pytest.mark.parametrize("not_sensitive_gate", ["code", "markdown"])
    def test_a_gate_the_baseline_found_not_sensitive_makes_the_later_arm_inconclusive(
        self, tmp_path: Path, monkeypatch, not_sensitive_gate: str,
    ) -> None:
        certification = self._certification(
            tmp_path, monkeypatch, gate_verdicts={not_sensitive_gate: analysis.SENSITIVITY_NOT_SENSITIVE},
        )

        assert certification == analysis.CERTIFICATION_INCONCLUSIVE

    @pytest.mark.parametrize("fixtures", [0, 1])
    def test_a_null_verdict_for_a_gate_of_fewer_than_two_fixtures_is_valid_and_never_read_as_sensitive(
        self, tmp_path: Path, monkeypatch, fixtures: int,
    ) -> None:
        certification = self._certification(
            tmp_path, monkeypatch, gate_verdicts={"markdown": None}, fixture_counts={"markdown": fixtures},
        )

        assert certification == analysis.CERTIFICATION_INCONCLUSIVE

    def test_an_empty_gate_is_short_so_the_later_arm_is_inconclusive_without_raising(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        certification = self._certification(
            tmp_path, monkeypatch, gate_verdicts={"markdown": None}, fixture_counts={"markdown": 0},
            defect_counts=(2, 0),
        )

        assert certification == analysis.CERTIFICATION_INCONCLUSIVE


class TestAnalyzeLaterArmRefusesAnUnusableBaselineReport:
    """A baseline report that cannot be read, lacks a key, or was computed under another
    defects.json or K exits 2, and is never read as sensitive."""

    def _exit_code_and_stderr(self, tmp_path: Path, monkeypatch, capsys, edit_report) -> tuple[int, str]:
        """Runs a later-arm `analyze` after `edit_report(report_path, conditions)` has edited the baseline report."""
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_records(tmp_path)
        edit_report(files["baseline_report"], json.loads(files["conditions"].read_text()))
        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))
        return exit_code, capsys.readouterr().err

    def test_a_missing_file_exits_2_naming_the_path(self, tmp_path: Path, monkeypatch, capsys) -> None:
        exit_code, stderr = self._exit_code_and_stderr(
            tmp_path, monkeypatch, capsys, lambda report_path, conditions: report_path.unlink(),
        )

        assert exit_code == 2
        assert "baseline-report.json" in stderr
        assert "unreadable" in stderr

    def test_an_unparseable_file_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        exit_code, stderr = self._exit_code_and_stderr(
            tmp_path, monkeypatch, capsys, lambda report_path, conditions: report_path.write_text("not json"),
        )

        assert exit_code == 2
        assert "unreadable" in stderr

    @pytest.mark.parametrize("missing_gate", ["code", "markdown"])
    def test_a_missing_verdict_key_exits_2(self, tmp_path: Path, monkeypatch, capsys, missing_gate: str) -> None:
        def drop_verdict(report_path: Path, conditions: dict) -> None:
            report = json.loads(report_path.read_text())
            del report["gates"][missing_gate]["baseline_sensitivity"]["verdict"]
            report_path.write_text(json.dumps(report))

        exit_code, stderr = self._exit_code_and_stderr(tmp_path, monkeypatch, capsys, drop_verdict)

        assert exit_code == 2
        assert "verdict" in stderr

    def test_a_null_verdict_beside_two_or_more_fixtures_exits_2_instead_of_reading_as_sensitive(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        def null_the_code_verdict(report_path: Path, conditions: dict) -> None:
            report_path.write_text(json.dumps(
                _baseline_report(conditions, gate_verdicts={"code": None}, fixture_counts={"code": 2}),
            ))

        exit_code, stderr = self._exit_code_and_stderr(tmp_path, monkeypatch, capsys, null_the_code_verdict)

        assert exit_code == 2
        assert "null verdict" in stderr

    @pytest.mark.parametrize(
        ("mutate", "expected_field"),
        [
            pytest.param(lambda identity: identity.update(defects_json_hash="0" * 64), "defects_json_hash",
                         id="defects-digest"),
            pytest.param(lambda identity: identity.update(k=99), "'k'", id="k"),
            pytest.param(lambda identity: identity.update(harness_closure_hash="0" * 64), "harness_closure_hash",
                         id="closure-hash"),
            pytest.param(lambda identity: identity["arm_dir_hashes"].update({arms_mod.ARM_FUNCTION_CONTEXT: "0" * 64}),
                         f"arm_dir_hashes[{arms_mod.ARM_FUNCTION_CONTEXT}]", id="an-arm-dir-hash"),
            pytest.param(lambda identity: identity["judge_agent_hashes"].update({"bench-judge-precision": "0" * 64}),
                         "judge_agent_hashes[bench-judge-precision]", id="a-judge-hash"),
            pytest.param(lambda identity: identity["environment"].update(cli_version="1.0.0"),
                         "environment[cli_version]", id="cli-version"),
            pytest.param(lambda identity: identity["environment"].update(ambient_config_commit="0ld"),
                         "environment[ambient_config_commit]", id="ambient-config-commit"),
        ],
    )
    def test_a_report_computed_under_another_freeze_exits_2_naming_the_field_and_the_regeneration(
        self, tmp_path: Path, monkeypatch, capsys, mutate, expected_field: str,
    ) -> None:
        def make_stale(report_path: Path, conditions: dict) -> None:
            report = _baseline_report(conditions)
            mutate(report["freeze_identity"])
            report_path.write_text(json.dumps(report))

        exit_code, stderr = self._exit_code_and_stderr(tmp_path, monkeypatch, capsys, make_stale)

        assert exit_code == 2
        assert "invalidated -- rerun all arms" in stderr
        assert expected_field in stderr
        assert "rerun all arms under the new freeze, then regenerate the baseline report" in stderr

    def test_a_report_matching_in_every_compared_part_is_accepted_after_the_campaign_seed_and_main_commit_change(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """`campaign_seed` and `main_commit_sha` are not part of a freeze identity, so changing them in
        conditions.json after the report was made does not invalidate the report."""
        files = _frozen_files(tmp_path, monkeypatch, campaign_seed=7)
        conditions_at_report_time = {**json.loads(files["conditions"].read_text()), "main_commit_sha": "a" * 40}
        files["baseline_report"].write_text(json.dumps(_baseline_report(conditions_at_report_time)))
        files["conditions"].write_text(json.dumps(
            {**conditions_at_report_time, "campaign_seed": 8, "main_commit_sha": "b" * 40},
        ))
        reviewer_path, judge_path = _judged_records(tmp_path)

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 0, capsys.readouterr().err

    def test_a_report_without_a_freeze_identity_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        def drop_identity(report_path: Path, conditions: dict) -> None:
            report = _baseline_report(conditions)
            del report["freeze_identity"]
            report_path.write_text(json.dumps(report))

        exit_code, stderr = self._exit_code_and_stderr(tmp_path, monkeypatch, capsys, drop_identity)

        assert exit_code == 2
        assert "freeze_identity" in stderr


class TestAnalyzeBaselineThenLaterArmThroughTheWrittenReport:
    """The baseline report is written by baseline-mode `analyze --out` and read by a later arm's `analyze`
    through `--baseline-report-path`, so the writer's keys and the reader's keys are tested together.
    A gate whose defects only arm 1 finds reads sensitive in the baseline, and a gate whose defects both
    arms find reads not sensitive. The patched N_min keeps no gate short, so each outcome comes
    from the baseline verdicts alone."""

    @pytest.fixture(autouse=True)
    def _no_gate_is_short(self, patch_n_min) -> None:
        patch_n_min(_N_MIN_NO_GATE_IS_SHORT)

    @staticmethod
    def _write_baseline_report(
        tmp_path: Path, files: dict[str, Path], confirmed: list[ConfirmedDefect], *, gap_gates: tuple[str, ...],
    ) -> Path:
        """Runs baseline-mode `analyze --out`; the defects in `gap_gates` are found by arm 1 alone."""
        table = {
            defect.id: _ONLY_ARM_1_FINDS if ("markdown" if defect.file_is_markdown else "code") in gap_gates
            else _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING
            for defect in confirmed
        }
        reviewer_path, judge_path = _judged_table(tmp_path, table, subdir="baseline")
        report_path = tmp_path / "written-baseline.json"
        args = run_review_bench.build_parser().parse_args([
            "analyze", "--defects-path", str(files["defects"]), "--reviewer-records-path", str(reviewer_path),
            "--judge-records-path", str(judge_path), "--k", "1", "--out", str(report_path),
            "--arms-root", str(files["arms"]),
        ])
        assert run_review_bench.cmd_analyze(args) == 0
        return report_path

    def _later_arm_report(self, tmp_path: Path, monkeypatch, *, gap_gates: tuple[str, ...]) -> dict:
        """A later arm that finds everything, analyzed against a baseline report the writer produced."""
        confirmed = _two_gate_defects()
        files = _frozen_files(tmp_path, monkeypatch, confirmed=confirmed)
        files["baseline_report"] = self._write_baseline_report(tmp_path, files, confirmed, gap_gates=gap_gates)
        reviewer_path, judge_path = _judged_table(
            tmp_path, {defect.id: _BOTH_ARMS_FIND_WITH_ONE_VALID_FINDING for defect in confirmed}, subdir="later",
        )
        out_path = tmp_path / "later-arm-report.json"

        exit_code = run_review_bench.main(
            _analyze_argv(files, reviewer_path, judge_path, "--k", "1", "--out", str(out_path)),
        )

        assert exit_code == 0
        return json.loads(out_path.read_text())

    def test_a_baseline_report_with_the_markdown_gate_not_sensitive_leaves_a_passing_later_arm_inconclusive(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        report = self._later_arm_report(tmp_path, monkeypatch, gap_gates=("code",))

        assert report["certification"] == analysis.CERTIFICATION_INCONCLUSIVE
        assert report["gates"]["code"]["baseline_sensitivity"]["verdict"] == analysis.SENSITIVITY_SENSITIVE
        assert report["gates"]["markdown"]["baseline_sensitivity"]["verdict"] == analysis.SENSITIVITY_NOT_SENSITIVE
        assert (report["gates"]["code"]["outcome"], report["gates"]["markdown"]["outcome"]) == (
            analysis.GATE_OUTCOME_PASS, analysis.GATE_OUTCOME_INCONCLUSIVE,
        )

    def test_a_baseline_report_with_the_code_gate_not_sensitive_leaves_a_passing_later_arm_inconclusive(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        report = self._later_arm_report(tmp_path, monkeypatch, gap_gates=("markdown",))

        assert report["certification"] == analysis.CERTIFICATION_INCONCLUSIVE
        assert report["gates"]["code"]["baseline_sensitivity"]["verdict"] == analysis.SENSITIVITY_NOT_SENSITIVE
        assert report["gates"]["markdown"]["baseline_sensitivity"]["verdict"] == analysis.SENSITIVITY_SENSITIVE

    def test_the_control_with_both_gates_sensitive_and_no_gate_short_certifies(self, tmp_path: Path, monkeypatch) -> None:
        report = self._later_arm_report(tmp_path, monkeypatch, gap_gates=("code", "markdown"))

        assert report["certification"] == analysis.CERTIFICATION_CERTIFIED
        assert (report["gates"]["code"]["outcome"], report["gates"]["markdown"]["outcome"]) == (
            analysis.GATE_OUTCOME_PASS, analysis.GATE_OUTCOME_PASS,
        )


@pytest.mark.usefixtures("stubbed_environment")
class TestRunVerifiesFrozenConditionsBeforeDispatch:
    def _run_argv(self, tmp_path: Path, files: dict[str, Path], *extra: str) -> list[str]:
        return [
            "run", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--conditions-path", str(files["conditions"]), "--records-dir", str(tmp_path / "runs"),
            "--run-store-dir", str(tmp_path / "run-store"), "--k", "1", "--workers", "1", *extra,
        ]

    def _stub_campaign(self, monkeypatch) -> list[str]:
        campaigns_started: list[str] = []

        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            campaigns_started.append(campaign_id)
            return runner.CampaignResult(campaign_id=campaign_id, block_results={})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        return campaigns_started

    def test_unchanged_files_matching_k_and_environment_start_the_campaign(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        campaigns_started = self._stub_campaign(monkeypatch)

        assert run_review_bench.main(self._run_argv(tmp_path, files)) == 0

        assert len(campaigns_started) == 1

    def test_an_arm_body_edited_after_the_freeze_exits_2_before_any_dispatch(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        campaigns_started = self._stub_campaign(monkeypatch)
        (files["arms"] / arms_mod.ARM_FUNCTION_CONTEXT / "bench-staff-backend-engineer.md").write_text("edited\n")

        exit_code = run_review_bench.main(self._run_argv(tmp_path, files))

        assert exit_code == 2
        assert f"arm_dir_hashes[{arms_mod.ARM_FUNCTION_CONTEXT}]" in capsys.readouterr().err
        assert campaigns_started == []

    def test_a_k_other_than_the_frozen_k_exits_2_before_any_dispatch(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch, k=10)
        campaigns_started = self._stub_campaign(monkeypatch)

        exit_code = run_review_bench.main(self._run_argv(tmp_path, files))

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "differs from the frozen k 10" in stderr
        assert "rerun all arms" not in stderr
        assert campaigns_started == []

    def test_a_changed_cli_version_exits_2_before_any_dispatch(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        campaigns_started = self._stub_campaign(monkeypatch)
        monkeypatch.setattr(
            runner, "read_environment_record", lambda **_kwargs: runner.EnvironmentRecord("9.9.9", "deadbeef"),
        )

        exit_code = run_review_bench.main(self._run_argv(tmp_path, files))

        assert exit_code == 2
        assert "'9.9.9'" in capsys.readouterr().err
        assert campaigns_started == []

    def test_no_conditions_file_exits_2_before_any_dispatch_and_writes_no_records(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        files["conditions"].unlink()
        campaigns_started = self._stub_campaign(monkeypatch)

        exit_code = run_review_bench.main(self._run_argv(tmp_path, files))

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "no frozen conditions" in stderr
        assert "freeze" in stderr
        assert campaigns_started == []
        assert not (tmp_path / "runs").exists()  # no records file

    def test_a_frozen_conditions_file_yields_a_reference_holding_the_frozen_environment(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)

        reference = run_review_bench._verify_frozen_conditions(
            files["conditions"], defects_path=files["defects"], arms_root=files["arms"], k=1, label="run",
            require_conditions=True,
        )

        reference.require_match(_STUBBED_ENVIRONMENT, where="d1's block start")
        with pytest.raises(runner.EnvironmentMismatchError):
            reference.require_match(runner.EnvironmentRecord("9.9.9", "deadbeef"), where="d1's block end")

    def test_smoke_does_not_verify_frozen_conditions(self, tmp_path: Path, monkeypatch) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        campaigns_started = self._stub_campaign(monkeypatch)
        (files["arms"] / arms_mod.ARM_FUNCTION_CONTEXT / "bench-staff-backend-engineer.md").write_text("edited\n")

        exit_code = run_review_bench.main([
            "smoke", "--defect-id", "d1", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--records-dir", str(tmp_path / "runs"), "--run-store-dir", str(tmp_path / "run-store"),
            "--k", "1", "--workers", "1",
        ])

        assert exit_code == 0
        assert len(campaigns_started) == 1


@pytest.mark.usefixtures("stubbed_environment")
class TestJudgeVerifiesFrozenConditionsBeforeDispatch:
    def _judge_argv(self, tmp_path: Path, files: dict[str, Path], reviewer_path: Path) -> list[str]:
        return [
            "judge", "--defects-path", str(files["defects"]), "--arms-root", str(files["arms"]),
            "--conditions-path", str(files["conditions"]), "--reviewer-records-path", str(reviewer_path),
            "--judge-run-store-dir", str(tmp_path / "judge-run-store"), "--judge-records-dir", str(tmp_path / "judge-runs"),
        ]

    def _stub_run_defect_judges(self, monkeypatch) -> dict:
        """Stands in for the judge dispatch and returns the keyword arguments it received."""
        from review_bench import adjudicate

        captured: dict = {}

        def fake_run_defect_judges(defect, records, **kwargs):
            captured.update(kwargs)
            return (
                _run_record(defect.id, JUDGE_ARM_RECALL, "j-recall", "d1-a: NOT_FOUND"),
                _run_record(defect.id, JUDGE_ARM_PRECISION, "j-precision", "### Run d1-a\n"),
            )

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)
        return captured

    def test_a_judge_rubric_edited_after_the_freeze_exits_2_before_any_judge_runs(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, _judge_path = _judged_records(tmp_path)
        files["recall_judge"].write_text("edited rubric\n")

        def fail_if_a_judge_runs(*args, **kwargs):
            raise AssertionError("no judge may be dispatched after a failed frozen-conditions check")

        monkeypatch.setattr(adjudicate, "run_defect_judges", fail_if_a_judge_runs)

        exit_code = run_review_bench.main(self._judge_argv(tmp_path, files, reviewer_path))

        assert exit_code == 2
        assert "judge_agent_hashes[bench-judge-recall]" in capsys.readouterr().err

    def test_no_conditions_file_notes_it_and_holds_the_judge_blocks_to_their_first_reading(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        files["conditions"].unlink()
        reviewer_path, _judge_path = _judged_records(tmp_path)
        captured = self._stub_run_defect_judges(monkeypatch)

        exit_code = run_review_bench.main(self._judge_argv(tmp_path, files, reviewer_path))

        assert exit_code == 0
        assert "no frozen conditions" in capsys.readouterr().err
        reference = captured["environment_reference"]
        reference.require_match(_STUBBED_ENVIRONMENT, where="d1's judge block start")  # adopted
        with pytest.raises(runner.EnvironmentMismatchError, match="first reading"):
            reference.require_match(runner.EnvironmentRecord("9.9.9", "x"), where="d2's judge block start")

    def test_judge_hands_the_live_checkout_roots_to_every_defect_judge(self, tmp_path: Path, monkeypatch) -> None:
        live_roots = (Path("/live-checkout-sentinel"),)
        monkeypatch.setattr(runner, "default_live_checkout_roots", lambda: live_roots)
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, _judge_path = _judged_records(tmp_path)
        captured = self._stub_run_defect_judges(monkeypatch)

        assert run_review_bench.main(self._judge_argv(tmp_path, files, reviewer_path)) == 0

        assert captured["live_checkout_roots"] == live_roots

    def test_a_frozen_conditions_file_holds_the_judge_blocks_to_the_frozen_environment(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, _judge_path = _judged_records(tmp_path)
        captured = self._stub_run_defect_judges(monkeypatch)

        assert run_review_bench.main(self._judge_argv(tmp_path, files, reviewer_path)) == 0

        with pytest.raises(runner.EnvironmentMismatchError, match="frozen environment"):
            captured["environment_reference"].require_match(
                runner.EnvironmentRecord("9.9.9", "x"), where="d1's judge block start",
            )

    def test_a_judge_block_environment_mismatch_exits_2_and_writes_no_records(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, _judge_path = _judged_records(tmp_path)

        def halt(defect, records, **kwargs):
            raise runner.EnvironmentMismatchError("halted: d1's judge block end differs")

        monkeypatch.setattr(adjudicate, "run_defect_judges", halt)

        exit_code = run_review_bench.main(self._judge_argv(tmp_path, files, reviewer_path))

        assert exit_code == 2
        assert "halted: d1's judge block end differs" in capsys.readouterr().err
        assert not (tmp_path / "judge-runs").exists()


@pytest.mark.usefixtures("stubbed_environment")
class TestRunPreflightBeforeDispatch:
    def _stub_campaign(self, monkeypatch, capsys=None) -> list[str]:
        stderr_at_launch: list[str] = []

        def fake_run_campaign(defect_ids, *, campaign_id, **kwargs):
            stderr_at_launch.append(capsys.readouterr().err if capsys is not None else "")
            return runner.CampaignResult(campaign_id=campaign_id, block_results={})

        monkeypatch.setattr(runner, "run_campaign", fake_run_campaign)
        return stderr_at_launch

    def _argv(self, subcommand: str, tmp_path: Path, files: dict[str, Path], *extra: str) -> list[str]:
        path_flags = _frozen_conditions_flags(files) if subcommand == "run" else _defects_and_arms_flags(files)
        return [
            subcommand, *path_flags, "--records-dir", str(tmp_path / "runs"),
            "--run-store-dir", str(tmp_path / "run-store"), "--workers", "1", *extra,
        ]

    def test_an_unresolvable_commit_exits_2_before_any_dispatch(self, tmp_path: Path, monkeypatch, capsys) -> None:
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        _commit(repo, "introduce x")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        files = _frozen_files(tmp_path, monkeypatch)
        stderr_at_launch = self._stub_campaign(monkeypatch)

        exit_code = run_review_bench.main(self._argv("run", tmp_path, files, "--k", "1"))

        assert exit_code == 2
        assert "a" * 40 in capsys.readouterr().err
        assert stderr_at_launch == []

    def test_a_missing_arm_snapshot_exits_2_before_any_dispatch(self, tmp_path: Path, monkeypatch, capsys) -> None:
        """Run through `smoke`: `run` verifies the arm directories against the frozen hashes
        first, so a missing snapshot never reaches the preflight there."""
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "app.py", "x = 1\n")
        head_sha = _commit(repo, "introduce x")
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [
            ConfirmedDefect(
                id="d1", source="szz", lens="staff-backend-engineer", base_commit=head_sha, head_commit=head_sha,
                fix_commit=head_sha, fix_date="2024-01-01", description="test defect",
                path="app.py", file_is_markdown=False,
            ),
        ])
        files = {"defects": defects_path, "arms": tmp_path / "arms"}
        stderr_at_launch = self._stub_campaign(monkeypatch)

        exit_code = run_review_bench.main(self._argv("smoke", tmp_path, files, "--defect-id", "d1", "--k", "1"))

        assert exit_code == 2
        assert "arm snapshot" in capsys.readouterr().err
        assert stderr_at_launch == []

    @pytest.mark.parametrize("subcommand", ["run", "smoke"])
    def test_prints_the_cost_ceiling_before_the_first_dispatch(
        self, subcommand: str, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=("d1", "d2"), k=3)
        stderr_at_launch = self._stub_campaign(monkeypatch, capsys)

        exit_code = run_review_bench.main(
            self._argv(subcommand, tmp_path, files, "--defect-id", "d1", "--defect-id", "d2", "--k", "3"),
        )

        assert exit_code == 0
        single_attempt = 2 * 2 * 3 * runner.REVIEWER_BUDGET_CAP_USD
        assert (
            f"{subcommand}: 2 defect(s) x 2 arm(s) x K=3 = 12 run(s); nominal cap product ${single_attempt:,.2f} "
            f"at ${runner.REVIEWER_BUDGET_CAP_USD:.2f} per run (${single_attempt * 2:,.2f} if every run retries once) "
            "-- unverified as a bound on spend"
        ) in stderr_at_launch[0]
        assert "environment" not in stderr_at_launch[0]  # a halt reruns nothing, so the ceiling has no such multiplier

    def test_the_estimate_and_preflight_cover_only_defects_a_resume_still_has_to_run(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        preflighted: list[list[str]] = []
        monkeypatch.setattr(
            runner, "preflight_defects",
            lambda pending, **kwargs: preflighted.append([defect.id for defect in pending]),
        )
        files = _frozen_files(tmp_path, monkeypatch, defect_ids=("d1", "d2"), k=3)
        runner.RunStore(tmp_path / "run-store").mark_block_complete("d1")
        stderr_at_launch = self._stub_campaign(monkeypatch, capsys)

        assert run_review_bench.main(self._argv("run", tmp_path, files, "--k", "3")) == 0

        assert preflighted == [["d2"]]
        assert "1 defect(s) x 2 arm(s) x K=3 = 6 run(s)" in stderr_at_launch[0]


class TestSmokeRequiresADefectId:
    def test_smoke_without_a_defect_id_is_rejected_at_parse_time(self, capsys) -> None:
        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.build_parser().parse_args(["smoke"])

        assert exit_info.value.code == 2
        assert "--defect-id" in capsys.readouterr().err

    def test_smoke_with_a_defect_id_parses(self) -> None:
        args = run_review_bench.build_parser().parse_args(["smoke", "--defect-id", "d1"])

        assert args.defect_id == ["d1"]

    def test_run_still_defaults_to_every_confirmed_defect(self) -> None:
        args = run_review_bench.build_parser().parse_args(["run"])

        assert args.defect_id == []


@pytest.mark.usefixtures("stubbed_environment")
class TestRunAndSmokeSystemicFailureSignal:
    def _argv(self, subcommand: str, tmp_path: Path, files: dict[str, Path]) -> list[str]:
        path_flags = _frozen_conditions_flags(files) if subcommand == "run" else _defects_and_arms_flags(files)
        return [
            subcommand, "--defect-id", "d1", *path_flags, "--records-dir", str(tmp_path / "runs"),
            "--run-store-dir", str(tmp_path / "run-store"), "--k", "1", "--workers", "1",
        ]

    def test_the_final_line_reports_ok_and_missing_counts_by_reason(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        import dataclasses

        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        files = _frozen_files(tmp_path, monkeypatch)
        ok_record = _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "finding")
        missing_record = dataclasses.replace(
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "", status="missing"),
            missing_reason=runner.VALIDITY_FAIL_RESULT_ERROR, attempts=2,
        )
        block = runner.BlockResult(records=(ok_record, missing_record), representative_session_id_by_arm={})
        monkeypatch.setattr(
            runner, "run_campaign",
            lambda defect_ids, *, campaign_id, **kwargs: runner.CampaignResult(campaign_id, {"d1": block}),
        )

        assert run_review_bench.main(self._argv("run", tmp_path, files)) == 0

        assert "ran 2 run(s) across 1 defect(s): 1 ok, 1 missing (result-error x1), 1 retried" in capsys.readouterr().err

    def test_a_halted_campaign_exits_2_and_prints_the_reason(self, tmp_path: Path, monkeypatch, capsys) -> None:
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        files = _frozen_files(tmp_path, monkeypatch)

        def halt(defect_ids, **kwargs):
            raise runner.SystemicFailureError("d1: all 2 run(s) are missing")

        monkeypatch.setattr(runner, "run_campaign", halt)

        assert run_review_bench.main(self._argv("run", tmp_path, files)) == 2
        assert "d1: all 2 run(s) are missing" in capsys.readouterr().err


class TestJudgeSkipsDefectsWithNoCompletedReviewerRun:
    def _judge_argv(self, tmp_path: Path, defects_path: Path, reviewer_path: Path) -> list[str]:
        return [
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"), "--judge-run-store-dir", str(tmp_path / "store"),
            "--judge-records-dir", str(tmp_path / "judge-runs"), "--campaign-id", "c1",
        ]

    def test_a_defect_whose_reviewer_runs_are_all_missing_dispatches_no_judge(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_path, [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "", status="missing"),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "", status="missing"),
        ])

        def fail_if_a_judge_runs(*args, **kwargs):
            raise AssertionError("no judge may run over a defect with no completed reviewer run")

        monkeypatch.setattr(adjudicate, "run_defect_judges", fail_if_a_judge_runs)

        exit_code = run_review_bench.main(self._judge_argv(tmp_path, defects_path, reviewer_path))

        assert exit_code == 0
        assert "judge: no completed reviewer run for d1 -- skipping" in capsys.readouterr().err

    def test_a_defect_with_at_least_one_completed_run_is_still_judged(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import adjudicate

        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_path, [
            _run_record("d1", arms_mod.ARM_CURRENT_RULE, "run-a", "finding"),
            _run_record("d1", arms_mod.ARM_FUNCTION_CONTEXT, "run-b", "", status="missing"),
        ])
        judged: list[str] = []

        def fake_run_defect_judges(defect, records, **kwargs):
            judged.append(defect.id)
            return (
                _run_record(defect.id, JUDGE_ARM_RECALL, "j-recall", "run-a: NOT_FOUND"),
                _run_record(defect.id, JUDGE_ARM_PRECISION, "j-precision", "### Run run-a\n"),
            )

        monkeypatch.setattr(adjudicate, "run_defect_judges", fake_run_defect_judges)

        assert run_review_bench.main(self._judge_argv(tmp_path, defects_path, reviewer_path)) == 0

        assert judged == ["d1"]


def _candidate(**overrides) -> defects.Candidate:
    kwargs = dict(
        id="c1", source="szz", lens="staff-backend-engineer", base_commit="a" * 40, head_commit="b" * 40,
        fix_commit="c" * 40, fix_date="2024-01-01", lines_exist_at_introducing_head=True,
        reviewer_could_have_caught_it=True, file_is_markdown=False,
    )
    kwargs.update(overrides)
    return defects.Candidate(**kwargs)


class TestCmdMineSzz:
    def test_writes_the_mined_candidates_and_passes_base_ref_through(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_szz

        candidate = _candidate(id="szz-1")
        captured_calls: list[tuple[Path, str]] = []

        def fake_mine(repo_root, *, base_ref):
            captured_calls.append((repo_root, base_ref))
            return [candidate]

        monkeypatch.setattr(mine_szz, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = argparse.Namespace(base_ref="origin/main", local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_szz(args)

        assert exit_code == 0
        assert captured_calls == [(tmp_path / "repo", "origin/main")]
        out_path = tmp_path / "local" / "szz_candidates.json"
        assert defects.load_candidates(out_path) == [candidate]

    def test_prints_the_written_candidate_count_and_path(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from review_bench import mine_szz

        monkeypatch.setattr(mine_szz, "mine", lambda repo_root, *, base_ref: [_candidate(), _candidate(id="c2")])

        out_path = tmp_path / "local" / "szz_candidates.json"
        args = argparse.Namespace(base_ref="origin/main", local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_szz(args)

        assert exit_code == 0
        assert f"mine-szz: wrote 2 candidate(s) to {out_path}" in capsys.readouterr().err

    def test_cli_flags_reach_cmd_mine_szz_via_build_parser(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_szz

        captured_calls: list[tuple[Path, str]] = []

        def fake_mine(repo_root, *, base_ref):
            captured_calls.append((repo_root, base_ref))
            return []

        monkeypatch.setattr(mine_szz, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = run_review_bench.build_parser().parse_args(
            ["mine-szz", "--base-ref", "some-ref", "--local-dir", str(tmp_path / "local")],
        )
        exit_code = run_review_bench.cmd_mine_szz(args)

        assert exit_code == 0
        assert captured_calls == [(tmp_path / "repo", "some-ref")]


class TestCmdMineRounds:
    def test_writes_the_mined_candidates(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_review_rounds

        candidate = _candidate(id="round-1", source="review-round")
        captured_calls: list[Path] = []

        def fake_mine(repo_root):
            captured_calls.append(repo_root)
            return [candidate]

        monkeypatch.setattr(mine_review_rounds, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = argparse.Namespace(local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_rounds(args)

        assert exit_code == 0
        assert captured_calls == [tmp_path / "repo"]
        out_path = tmp_path / "local" / "review_round_candidates.json"
        assert defects.load_candidates(out_path) == [candidate]

    def test_prints_the_written_candidate_count_and_path(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from review_bench import mine_review_rounds

        monkeypatch.setattr(mine_review_rounds, "mine", lambda repo_root: [])

        out_path = tmp_path / "local" / "review_round_candidates.json"
        args = argparse.Namespace(local_dir=str(tmp_path / "local"))
        exit_code = run_review_bench.cmd_mine_rounds(args)

        assert exit_code == 0
        assert f"mine-rounds: wrote 0 candidate(s) to {out_path}" in capsys.readouterr().err

    def test_cli_flags_reach_cmd_mine_rounds_via_build_parser(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_review_rounds

        captured_calls: list[Path] = []

        def fake_mine(repo_root):
            captured_calls.append(repo_root)
            return []

        monkeypatch.setattr(mine_review_rounds, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = run_review_bench.build_parser().parse_args(["mine-rounds", "--local-dir", str(tmp_path / "local")])
        exit_code = run_review_bench.cmd_mine_rounds(args)

        assert exit_code == 0
        assert captured_calls == [tmp_path / "repo"]


class TestCmdMinePrComments:
    def test_writes_the_mined_candidates_under_the_glob_confirm_reads(self, tmp_path: Path, monkeypatch, capsys) -> None:
        from review_bench import mine_pr_comments

        candidate = _candidate(id="pr-comment:1", source="pr-comment")
        captured_calls: list[Path] = []

        def fake_mine(repo_root):
            captured_calls.append(repo_root)
            return [candidate]

        monkeypatch.setattr(mine_pr_comments, "mine", fake_mine)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", tmp_path / "repo")

        args = run_review_bench.build_parser().parse_args(["mine-pr-comments", "--local-dir", str(tmp_path / "local")])
        exit_code = run_review_bench.cmd_mine_pr_comments(args)

        assert exit_code == 0
        assert captured_calls == [tmp_path / "repo"]
        out_path = tmp_path / "local" / "pr_comment_candidates.json"
        assert defects.load_candidates(out_path) == [candidate]
        assert list((tmp_path / "local").glob("*_candidates.json")) == [out_path]
        assert f"mine-pr-comments: wrote 1 candidate(s) to {out_path}" in capsys.readouterr().err

    def test_a_refused_run_exits_2_and_writes_no_shortlist(self, tmp_path: Path, monkeypatch) -> None:
        from review_bench import mine_pr_comments

        def refusing_mine(repo_root):
            raise SystemExit(2)

        monkeypatch.setattr(mine_pr_comments, "mine", refusing_mine)
        args = run_review_bench.build_parser().parse_args(["mine-pr-comments", "--local-dir", str(tmp_path / "local")])

        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.cmd_mine_pr_comments(args)

        assert exit_info.value.code == 2
        assert not (tmp_path / "local").exists()


class TestCmdMinePrCommentsKeepsAnExistingShortlistWhenTheRunFails:
    """`mine` exits 2 before the CLI writes anything, so a failed or degraded run leaves the previous
    shortlist as it was. `gh` is the fake from the miner's own tests, behind the real `mine`."""

    _EXISTING = [_candidate(id="pr-comment:earlier-run", source="pr-comment")]

    def _run_with_fake_gh(self, tmp_path: Path, monkeypatch, repo: Path, thread: list[dict], **fake_gh_kwargs) -> Path:
        from review_bench import mine_pr_comments
        from test_review_bench_mining import _MERGED_PR_NUMBER, _closed_pull, _FakeGh

        fake_gh_kwargs.setdefault("closed_pulls", [_closed_pull(_MERGED_PR_NUMBER, "feat")])
        fake_gh = _FakeGh(comments=thread, **fake_gh_kwargs)
        real_mine = mine_pr_comments.mine
        monkeypatch.setattr(mine_pr_comments, "mine", lambda repo_root: real_mine(repo_root, run=fake_gh))
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        shortlist = tmp_path / "local" / "pr_comment_candidates.json"
        defects.save_candidates(shortlist, self._EXISTING)
        args = run_review_bench.build_parser().parse_args(["mine-pr-comments", "--local-dir", str(tmp_path / "local")])
        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.cmd_mine_pr_comments(args)
        assert exit_info.value.code == 2
        return shortlist

    @pytest.mark.parametrize("failing_call_index", [2, 3], ids=["comments-listing", "closed-pulls-listing"])
    def test_a_listing_call_that_exits_non_zero_leaves_the_shortlist_unchanged(
        self, tmp_path: Path, monkeypatch, failing_call_index: int,
    ) -> None:
        from test_review_bench_mining import _pr_comment_repo

        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)

        shortlist = self._run_with_fake_gh(
            tmp_path, monkeypatch, repo, [], nonzero_exit_on_call_index=failing_call_index,
        )

        assert defects.load_candidates(shortlist) == self._EXISTING

    def test_a_fetch_failed_pr_head_exits_2_naming_the_pr_and_leaves_the_shortlist_byte_for_byte(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from test_review_bench_mining import (
            _MERGED_PR_NUMBER,
            _closed_pull,
            _fixed_reply_body,
            _pr_comment_repo,
            _pr_review_comment,
            _pr_thread_reply,
        )

        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))]
        expected_shortlist = tmp_path / "expected_shortlist.json"
        defects.save_candidates(expected_shortlist, self._EXISTING)

        shortlist = self._run_with_fake_gh(
            tmp_path, monkeypatch, repo, thread,
            closed_pulls=[_closed_pull(_MERGED_PR_NUMBER, "branch-with-no-local-ref")],
        )

        assert f"#{_MERGED_PR_NUMBER} could not be fetched" in capsys.readouterr().err
        assert defects.load_candidates(shortlist) == self._EXISTING
        assert shortlist.read_bytes() == expected_shortlist.read_bytes()
        assert list(shortlist.parent.iterdir()) == [shortlist]

    def test_a_run_with_one_unfetchable_pr_head_beside_a_minable_comment_writes_nothing_and_names_only_that_pr(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        """A partial shortlist would stand in for a complete one, so the comment that could be mined is
        not written either."""
        from test_review_bench_mining import (
            _MERGED_PR_NUMBER,
            _closed_pull,
            _fixed_thread_on_pull_request,
            _pr_comment_repo,
        )

        unfetchable_pr_number = _MERGED_PR_NUMBER + 2
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [
            *_fixed_thread_on_pull_request(introducing_sha, fix_sha, comment_id=1001, pr_number=_MERGED_PR_NUMBER),
            *_fixed_thread_on_pull_request(introducing_sha, fix_sha, comment_id=1002, pr_number=unfetchable_pr_number),
        ]
        expected_shortlist = tmp_path / "expected_shortlist.json"
        defects.save_candidates(expected_shortlist, self._EXISTING)

        shortlist = self._run_with_fake_gh(
            tmp_path, monkeypatch, repo, thread,
            closed_pulls=[
                _closed_pull(_MERGED_PR_NUMBER, "feat"), _closed_pull(unfetchable_pr_number, "branch-with-no-local-ref"),
            ],
        )

        stderr = capsys.readouterr().err
        assert "1 candidate(s) from 4 comment(s)" in stderr  # the fetchable thread was mined, then discarded
        (message,) = (line for line in stderr.splitlines() if "could not be fetched" in line)
        assert message.startswith(f"mine-pr-comments: 1 comment(s) skipped: PR head(s) #{unfetchable_pr_number} could not")
        assert f"#{_MERGED_PR_NUMBER}" not in message
        assert shortlist.read_bytes() == expected_shortlist.read_bytes()
        assert list(shortlist.parent.iterdir()) == [shortlist]


class TestCmdSnapshotArms:
    def test_writes_both_arms_under_the_given_root_and_reports_each(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        captured_calls: list[tuple[str, Path]] = []
        monkeypatch.setattr(
            arms_mod, "write_arm_snapshot", lambda arm, dest_dir: captured_calls.append((arm, dest_dir)),
        )

        arms_root = tmp_path / "arms"
        exit_code = run_review_bench.cmd_snapshot_arms(argparse.Namespace(arms_root=str(arms_root)))

        assert exit_code == 0
        assert captured_calls == [
            (arms_mod.ARM_CURRENT_RULE, arms_root / arms_mod.ARM_CURRENT_RULE),
            (arms_mod.ARM_FUNCTION_CONTEXT, arms_root / arms_mod.ARM_FUNCTION_CONTEXT),
        ]
        stderr = capsys.readouterr().err
        lens_count = len(arms_mod.LENS_READ_CLAUSES)
        assert (
            f"snapshot-arms: wrote {lens_count} lens file(s) for {arms_mod.ARM_CURRENT_RULE} "
            f"under {arms_root / arms_mod.ARM_CURRENT_RULE}"
        ) in stderr
        assert (
            f"snapshot-arms: wrote {lens_count} lens file(s) for {arms_mod.ARM_FUNCTION_CONTEXT} "
            f"under {arms_root / arms_mod.ARM_FUNCTION_CONTEXT}"
        ) in stderr

    def test_cli_flags_reach_cmd_snapshot_arms_via_build_parser(self, tmp_path: Path, monkeypatch) -> None:
        captured_calls: list[tuple[str, Path]] = []
        monkeypatch.setattr(
            arms_mod, "write_arm_snapshot", lambda arm, dest_dir: captured_calls.append((arm, dest_dir)),
        )

        arms_root = tmp_path / "arms"
        args = run_review_bench.build_parser().parse_args(["snapshot-arms", "--arms-root", str(arms_root)])
        exit_code = run_review_bench.cmd_snapshot_arms(args)

        assert exit_code == 0
        assert captured_calls == [
            (arms_mod.ARM_CURRENT_RULE, arms_root / arms_mod.ARM_CURRENT_RULE),
            (arms_mod.ARM_FUNCTION_CONTEXT, arms_root / arms_mod.ARM_FUNCTION_CONTEXT),
        ]


def _spot_check_args(tmp_path: Path, **overrides) -> argparse.Namespace:
    kwargs = dict(
        reviewer_records_path=str(tmp_path / "reviewer.jsonl"), judge_records_path=str(tmp_path / "judge.jsonl"),
        seed=0,
    )
    kwargs.update(overrides)
    return argparse.Namespace(**kwargs)


class TestCmdSpotCheckExport:
    def test_writes_the_sample_without_judge_label_or_arm_and_reports_counts(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        precision_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1:0", kind=adjudicate.SPOT_CHECK_KIND_PRECISION, judge_label="VALID",
                display_text="precision text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(
            run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, precision_sample),
        )

        out_path = tmp_path / "export.json"
        args = _spot_check_args(tmp_path, out=str(out_path))
        exit_code = run_review_bench.cmd_spot_check_export(args)

        assert exit_code == 0
        exported = json.loads(out_path.read_text())
        assert exported == [
            {"item_id": "d1:r1", "kind": adjudicate.SPOT_CHECK_KIND_RECALL, "display_text": "recall text"},
            {"item_id": "d1:r1:0", "kind": adjudicate.SPOT_CHECK_KIND_PRECISION, "display_text": "precision text"},
        ]
        stderr = capsys.readouterr().err
        assert f"spot-check export: wrote 1 recall item(s) and 1 precision item(s) to {out_path}" in stderr

    def test_cli_flags_reach_cmd_spot_check_export_via_build_parser(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, []))

        out_path = tmp_path / "export.json"
        args = run_review_bench.build_parser().parse_args([
            "spot-check", "export", "--reviewer-records-path", str(tmp_path / "reviewer.jsonl"),
            "--judge-records-path", str(tmp_path / "judge.jsonl"), "--out", str(out_path),
        ])
        exit_code = run_review_bench.cmd_spot_check_export(args)

        assert exit_code == 0
        exported = json.loads(out_path.read_text())
        assert exported == [{"item_id": "d1:r1", "kind": adjudicate.SPOT_CHECK_KIND_RECALL, "display_text": "recall text"}]


class TestCmdSpotCheckImport:
    def test_scores_kappa_per_kind_and_split_agreement_per_arm(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        precision_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1:0", kind=adjudicate.SPOT_CHECK_KIND_PRECISION, judge_label="VALID",
                display_text="precision text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(
            run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, precision_sample),
        )

        labels_path = tmp_path / "labels.json"
        labels_path.write_text(json.dumps([
            {"item_id": "d1:r1", "human_label": "FOUND"},
            {"item_id": "d1:r1:0", "human_label": "VALID", "split_ok": True},
        ]))

        args = _spot_check_args(tmp_path, labels_path=str(labels_path))
        exit_code = run_review_bench.cmd_spot_check_import(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"spot-check import: {adjudicate.SPOT_CHECK_KIND_RECALL} kappa = 1.000 -- validated" in stderr
        assert f"spot-check import: {adjudicate.SPOT_CHECK_KIND_PRECISION} kappa = 1.000 -- validated" in stderr
        assert f"spot-check import: {arms_mod.ARM_CURRENT_RULE} split agreement = 1.000" in stderr

    def test_cli_flags_reach_cmd_spot_check_import_via_build_parser(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, []))

        labels_path = tmp_path / "labels.json"
        labels_path.write_text(json.dumps([{"item_id": "d1:r1", "human_label": "FOUND"}]))

        args = run_review_bench.build_parser().parse_args([
            "spot-check", "import", "--reviewer-records-path", str(tmp_path / "reviewer.jsonl"),
            "--judge-records-path", str(tmp_path / "judge.jsonl"), "--labels-path", str(labels_path),
        ])
        exit_code = run_review_bench.cmd_spot_check_import(args)

        assert exit_code == 0
        stderr = capsys.readouterr().err
        assert f"spot-check import: {adjudicate.SPOT_CHECK_KIND_RECALL} kappa = 1.000 -- validated" in stderr


class TestMainCatchesHarnessInvalidatedErrorForJudgeAndSpotCheckExport:
    """`cmd_judge` and `_build_spot_check_samples` (unlike `cmd_analyze`) have
    no local `HarnessInvalidatedError` catch of their own -- this exercises
    `main`'s own top-level handler, the only thing standing between a
    malformed records file and a raw traceback for those commands."""

    def test_judge_with_a_malformed_reviewer_records_file_exits_2_via_main(
        self, tmp_path: Path, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        # Missing campaign_id/arm/etc. -- a schema failure, not a
        # truncated-write one, so read_run_records raises even as the last line.
        reviewer_records_path.write_text(json.dumps({"defect_id": "d1"}) + "\n")

        exit_code = run_review_bench.main([
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_records_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"),
        ])

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "malformed line 1" in stderr
        assert str(reviewer_records_path) in stderr

    def test_judge_with_a_malformed_judge_records_file_releases_the_lock_and_exits_2_via_main(
        self, tmp_path: Path, capsys,
    ) -> None:
        """cmd_judge reads judge_records_path inside the try/finally that
        holds run_store's lock -- unlike reviewer_records_path, read
        before acquire_lock. Proves the lock still releases when the
        exception fires from inside that block."""
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, (_run_record("d1", "current-rule", "run-1", "No findings."),))

        judge_records_dir = tmp_path / "judge-runs"
        judge_records_dir.mkdir()
        campaign_id = "judge-test"
        judge_records_path = judge_records_dir / f"{campaign_id}.jsonl"
        # Missing campaign_id/arm/etc. -- a schema failure, not a
        # truncated-write one, so read_run_records raises even as the last line.
        judge_records_path.write_text(json.dumps({"defect_id": "d1"}) + "\n")
        judge_run_store_dir = tmp_path / "judge-run-store"

        exit_code = run_review_bench.main([
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_records_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"),
            "--judge-records-dir", str(judge_records_dir), "--campaign-id", campaign_id,
            "--judge-run-store-dir", str(judge_run_store_dir),
        ])

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "malformed line 1" in stderr
        assert str(judge_records_path) in stderr
        runner.RunStore(judge_run_store_dir).acquire_lock()  # does not raise: the failed run released it

    def test_spot_check_export_with_a_malformed_judge_records_file_exits_2_via_main(
        self, tmp_path: Path, capsys,
    ) -> None:
        """_build_spot_check_samples' two runner.read_run_records calls
        have no local try/except in either cmd_spot_check_export or
        cmd_spot_check_import -- both rely entirely on main's top-level
        handler."""
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(reviewer_records_path, (_run_record("d1", "current-rule", "run-1", "No findings."),))
        judge_records_path = tmp_path / "judge.jsonl"
        # Missing campaign_id/arm/etc. -- a schema failure, not a
        # truncated-write one, so read_run_records raises even as the last line.
        judge_records_path.write_text(json.dumps({"defect_id": "d1"}) + "\n")

        exit_code = run_review_bench.main([
            "spot-check", "export", "--reviewer-records-path", str(reviewer_records_path),
            "--judge-records-path", str(judge_records_path), "--out", str(tmp_path / "sheet.json"),
        ])

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "malformed line 1" in stderr
        assert str(judge_records_path) in stderr


class TestJudgeRunStoreLock:
    def _judge_argv(self, tmp_path: Path, *, campaign_id: str, run_store_dir: Path | None, reviewer_status: str) -> list[str]:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        reviewer_records_path = tmp_path / "reviewer.jsonl"
        runner.append_run_records(
            reviewer_records_path, (_run_record("d1", "current-rule", "run-1", "No findings.", status=reviewer_status),),
        )
        judge_records_dir = tmp_path / "judge-runs"
        judge_records_dir.mkdir()
        argv = [
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(reviewer_records_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"),
            "--judge-records-dir", str(judge_records_dir), "--campaign-id", campaign_id,
        ]
        if run_store_dir is not None:
            argv += ["--judge-run-store-dir", str(run_store_dir)]
        return argv

    def test_judge_refuses_a_held_store_before_any_sweep_or_dispatch(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        from review_bench import adjudicate

        judge_run_store_dir = tmp_path / "judge-run-store"
        runner.RunStore(judge_run_store_dir).acquire_lock()  # held by this process for the test's duration
        sweeps: list[Path] = []
        dispatches: list[str] = []
        monkeypatch.setattr(runner.RunStore, "sweep_abandoned", lambda self, projects_root: sweeps.append(projects_root))
        monkeypatch.setattr(adjudicate, "run_defect_judges", lambda *args, **kwargs: dispatches.append("dispatched"))

        exit_code = run_review_bench.main(self._judge_argv(
            tmp_path, campaign_id="judge-test", run_store_dir=judge_run_store_dir, reviewer_status="ok",
        ))

        assert exit_code == 1
        stderr = capsys.readouterr().err
        assert "held by pid" in stderr
        assert sweeps == []
        assert dispatches == []

    def test_judge_under_another_campaign_id_is_not_blocked_by_a_held_default_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys,
    ) -> None:
        default_store_root = tmp_path / "default-judge-run-store"
        monkeypatch.setattr(run_review_bench, "DEFAULT_JUDGE_RUN_STORE_DIR", default_store_root)
        runner.RunStore(default_store_root / "a").acquire_lock()  # held by this process for the test's duration
        sweeps: list[Path] = []
        monkeypatch.setattr(
            runner.RunStore, "sweep_abandoned", lambda self, projects_root: sweeps.append(projects_root) or [],
        )

        exit_code = run_review_bench.main(self._judge_argv(
            tmp_path, campaign_id="b", run_store_dir=None, reviewer_status="missing",
        ))

        assert exit_code == 0
        assert len(sweeps) == 1
        assert "held by pid" not in capsys.readouterr().err


_COMMITTED_DEFAULT_PATH_NAMES = frozenset({
    "DEFAULT_DEFECTS_PATH", "DEFAULT_ARMS_ROOT", "DEFAULT_CONDITIONS_PATH", "DEFAULT_BASELINE_REPORT_PATH",
})
# Read at import: conftest's autouse fixture moves the `.local/` defaults under a tmp directory for each test.
_DEFAULT_PATHS = {name: value for name, value in vars(run_review_bench).items() if name.startswith("DEFAULT_")}


class TestDefaultOutputPathsAreGitIgnored:
    """Every default output path holds raw reviewer output or mined excerpts, so
    each must be git-ignored. A new `DEFAULT_*` path fails here until it is
    either ignored or named as committed."""

    @staticmethod
    def _is_git_ignored(path: Path) -> bool:
        # core.excludesFile is pointed at /dev/null so the verdict comes from the repo's own ignore rules,
        # not the host's global excludes.
        result = subprocess.run(
            ["git", "-c", "core.excludesFile=/dev/null", "check-ignore", "--no-index", "-q", "--", str(path)],
            cwd=run_review_bench.REPO_ROOT, check=False,
        )
        # 0 = ignored, 1 = not ignored, 128 = git error.
        assert result.returncode in (0, 1), f"git check-ignore failed with exit {result.returncode}"
        return result.returncode == 0

    @pytest.mark.parametrize("name", sorted(set(_DEFAULT_PATHS) - _COMMITTED_DEFAULT_PATH_NAMES))
    def test_a_default_output_path_is_git_ignored(self, name: str) -> None:
        # A child path: a directory-only pattern (`.local/`) matches a directory that exists,
        # so probing the directory itself passes or fails with the checkout's state.
        assert self._is_git_ignored(_DEFAULT_PATHS[name] / "probe"), f"{name} is not git-ignored"

    @pytest.mark.parametrize("name", sorted(_COMMITTED_DEFAULT_PATH_NAMES))
    def test_a_committed_default_path_is_not_git_ignored(self, name: str) -> None:
        assert not self._is_git_ignored(_DEFAULT_PATHS[name]), f"{name} is git-ignored"


class TestMissingRecordsPathExitsTwo:
    """`read_run_records` returns no records for a file that does not exist, so
    a mistyped records path would read as an empty campaign. Only `judge`'s own
    output path may be absent."""

    def test_judge_with_a_nonexistent_reviewer_records_path_exits_2_and_names_it(
        self, tmp_path: Path, capsys,
    ) -> None:
        defects_path = tmp_path / "defects.json"
        defects.save_confirmed_defects(defects_path, [_confirmed_single_defect()])
        mistyped_path = tmp_path / "reviewer-typo.jsonl"

        exit_code = run_review_bench.main([
            "judge", "--defects-path", str(defects_path), "--reviewer-records-path", str(mistyped_path),
            "--conditions-path", str(tmp_path / "no-conditions.json"),
            "--judge-run-store-dir", str(tmp_path / "store"), "--judge-records-dir", str(tmp_path / "judge-runs"),
        ])

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "does not exist" in stderr
        assert str(mistyped_path) in stderr
        assert not (tmp_path / "store").exists()

    @pytest.mark.parametrize("missing_side", ["reviewer", "judge"])
    def test_analyze_with_a_nonexistent_records_path_exits_2_and_names_it(
        self, missing_side: str, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        files = _frozen_files(tmp_path, monkeypatch)
        reviewer_path, judge_path = _judged_records(tmp_path)
        mistyped_path = tmp_path / "typo.jsonl"
        if missing_side == "reviewer":
            reviewer_path = mistyped_path
        else:
            judge_path = mistyped_path

        exit_code = run_review_bench.main(_analyze_argv(files, reviewer_path, judge_path, "--k", "1"))

        assert exit_code == 2
        stderr = capsys.readouterr().err
        assert "does not exist" in stderr
        assert str(mistyped_path) in stderr

    @pytest.mark.parametrize("missing_side", ["reviewer", "judge"])
    def test_spot_check_export_with_a_nonexistent_records_path_exits_2(
        self, missing_side: str, tmp_path: Path, capsys,
    ) -> None:
        reviewer_path, judge_path = _judged_records(tmp_path)
        mistyped_path = tmp_path / "typo.jsonl"

        exit_code = run_review_bench.main([
            "spot-check", "export",
            "--reviewer-records-path", str(mistyped_path if missing_side == "reviewer" else reviewer_path),
            "--judge-records-path", str(mistyped_path if missing_side == "judge" else judge_path),
            "--out", str(tmp_path / "export.json"),
        ])

        assert exit_code == 2
        assert str(mistyped_path) in capsys.readouterr().err
        assert not (tmp_path / "export.json").exists()


class TestMainRoutesTerminationSignalsToKeyboardInterrupt:
    """A `claude -p` child leads its own session, so a hangup never reaches it. `main()` sends
    SIGHUP and SIGTERM down the KeyboardInterrupt path that kills the child's process group."""

    @pytest.mark.parametrize("signum", [signal.SIGHUP, signal.SIGTERM])
    def test_the_signal_raises_keyboard_interrupt_once_main_has_run(self, signum, tmp_path: Path, monkeypatch) -> None:
        signal.signal(signum, signal.SIG_DFL)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: 0)

        assert run_review_bench.main(["mine-rounds", "--local-dir", str(tmp_path)]) == 0

        # Raising the signal with no handler installed would kill the pytest process.
        assert signal.getsignal(signum) not in (signal.SIG_DFL, signal.SIG_IGN)
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signum)

    def test_a_hangup_ignored_at_startup_stays_ignored_so_nohup_survives_logout(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: 0)

        assert run_review_bench.main(["mine-rounds", "--local-dir", str(tmp_path)]) == 0

        assert signal.getsignal(signal.SIGHUP) is signal.SIG_IGN
        signal.raise_signal(signal.SIGHUP)  # a handler installed over SIG_IGN would raise here

    @pytest.mark.parametrize(
        ("raised", "expected_exit_code"),
        [
            (KeyboardInterrupt(), 128 + signal.SIGINT),
            (run_review_bench._SignalInterrupt(signal.SIGTERM), 128 + signal.SIGTERM),
            (run_review_bench._SignalInterrupt(signal.SIGHUP), 128 + signal.SIGHUP),
        ],
    )
    def test_an_interrupted_command_exits_128_plus_the_signal_number_and_names_the_campaign_to_resume(
        self, raised: KeyboardInterrupt, expected_exit_code: int, monkeypatch, capsys,
    ) -> None:
        def interrupted_command(args: argparse.Namespace) -> int:
            args.campaign_id = "run-abc123"
            raise raised

        monkeypatch.setattr(run_review_bench, "cmd_run", interrupted_command)

        assert run_review_bench.main(["run"]) == expected_exit_code
        assert "run: interrupted -- resume with --campaign-id run-abc123" in capsys.readouterr().err

    def test_an_interrupt_before_any_campaign_was_chosen_offers_no_resume_hint(self, monkeypatch, capsys) -> None:
        def interrupted_command(args: argparse.Namespace) -> int:
            raise KeyboardInterrupt

        monkeypatch.setattr(run_review_bench, "cmd_run", interrupted_command)

        assert run_review_bench.main(["run"]) == 130
        assert "resume" not in capsys.readouterr().err

    def test_run_records_the_generated_campaign_id_on_the_args_main_reads(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        monkeypatch.setattr(runner, "preflight_defects", lambda *args, **kwargs: None)
        files = _frozen_files(tmp_path, monkeypatch, k=runner.DEFAULT_K)
        monkeypatch.setattr(runner, "read_environment_record", lambda **_kw: _STUBBED_ENVIRONMENT)

        def interrupted_campaign(*args, **kwargs):
            raise KeyboardInterrupt

        monkeypatch.setattr(runner, "run_campaign", interrupted_campaign)

        exit_code = run_review_bench.main([
            "run", *_frozen_conditions_flags(files), "--records-dir", str(tmp_path / "runs"),
            "--run-store-dir", str(tmp_path / "run-store"),
        ])

        assert exit_code == 130
        stderr = capsys.readouterr().err
        generated_id = stderr.split("run: campaign ", 1)[1].split(" ", 1)[0]
        assert generated_id.startswith("run-")
        assert f"resume with --campaign-id {generated_id}" in stderr


class TestMainRestrictsWrittenFilePermissions:
    def test_files_written_after_main_have_no_group_or_other_permission_bits(
        self, tmp_path: Path, monkeypatch,
    ) -> None:
        from review_bench import adjudicate

        recall_sample = [
            adjudicate.SpotCheckCandidate(
                item_id="d1:r1", kind=adjudicate.SPOT_CHECK_KIND_RECALL, judge_label="FOUND",
                display_text="recall text", arm=arms_mod.ARM_CURRENT_RULE,
            ),
        ]
        monkeypatch.setattr(run_review_bench, "_build_spot_check_samples", lambda args: (recall_sample, []))
        os.umask(0o022)
        exported_path = tmp_path / "local" / "export.json"
        records_path = tmp_path / "local" / "records.jsonl"

        exit_code = run_review_bench.main([
            "spot-check", "export", "--reviewer-records-path", str(tmp_path / "reviewer.jsonl"),
            "--judge-records-path", str(tmp_path / "judge.jsonl"), "--out", str(exported_path),
        ])
        runner.append_run_records(records_path, (_run_record("d1", "current-rule", "run-1", "No findings."),))

        assert exit_code == 0
        group_and_other_bits = stat.S_IRWXG | stat.S_IRWXO
        assert stat.S_IMODE(exported_path.stat().st_mode) & group_and_other_bits == 0
        assert stat.S_IMODE(records_path.stat().st_mode) & group_and_other_bits == 0
        assert stat.S_IMODE(exported_path.parent.stat().st_mode) & group_and_other_bits == 0

    @staticmethod
    def _run_main_over(local_dir: Path, monkeypatch) -> int:
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", local_dir)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: 0)
        return run_review_bench.main(["mine-rounds", "--local-dir", str(local_dir.parent / "elsewhere")])

    @pytest.mark.parametrize("loose_mode", [0o775, 0o750, 0o705])
    def test_a_pre_existing_local_dir_with_group_or_other_bits_is_tightened_to_owner_only(
        self, loose_mode: int, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        local_dir.chmod(loose_mode)

        assert self._run_main_over(local_dir, monkeypatch) == 0

        assert stat.S_IMODE(local_dir.stat().st_mode) == 0o700
        assert f"restricted {local_dir} from mode {loose_mode:04o} to 0700" in capsys.readouterr().err

    def test_an_owner_only_local_dir_is_left_alone_without_a_note(self, tmp_path: Path, monkeypatch, capsys) -> None:
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        local_dir.chmod(0o500)
        try:
            assert self._run_main_over(local_dir, monkeypatch) == 0

            assert stat.S_IMODE(local_dir.stat().st_mode) == 0o500
            assert "restricted" not in capsys.readouterr().err
        finally:
            local_dir.chmod(0o700)

    def test_an_absent_local_dir_is_not_created_by_the_check(self, tmp_path: Path, monkeypatch) -> None:
        local_dir = tmp_path / "local"

        assert self._run_main_over(local_dir, monkeypatch) == 0

        assert not local_dir.exists()

    def test_a_symlinked_local_dir_exits_2_and_leaves_the_link_target_untouched(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        link_target = tmp_path / "elsewhere"
        link_target.mkdir()
        link_target.chmod(0o775)
        local_dir = tmp_path / "local"
        local_dir.symlink_to(link_target, target_is_directory=True)
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", local_dir)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: pytest.fail("the command must not run"))

        assert run_review_bench.main(["mine-rounds"]) == 2

        assert "symlink or not a directory" in capsys.readouterr().err
        assert stat.S_IMODE(link_target.stat().st_mode) == 0o775

    def test_a_local_path_that_is_a_regular_file_exits_2(self, tmp_path: Path, monkeypatch, capsys) -> None:
        local_dir = tmp_path / "local"
        local_dir.write_text("not a directory")
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", local_dir)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: pytest.fail("the command must not run"))

        assert run_review_bench.main(["mine-rounds"]) == 2
        assert "symlink or not a directory" in capsys.readouterr().err

    def test_a_local_dir_under_a_regular_file_exits_2_rather_than_raising(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        (tmp_path / "blocker").write_text("not a directory")
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", tmp_path / "blocker" / "local")
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: pytest.fail("the command must not run"))

        assert run_review_bench.main(["mine-rounds"]) == 2
        assert "could not be inspected" in capsys.readouterr().err

    def test_a_local_dir_that_cannot_be_inspected_exits_2_rather_than_raising(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        local_dir = tmp_path / "local"

        def refuse_lstat(self, **_kwargs):
            raise PermissionError("no search permission")

        monkeypatch.setattr(Path, "lstat", refuse_lstat)
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", local_dir)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: pytest.fail("the command must not run"))

        assert run_review_bench.main(["mine-rounds"]) == 2
        assert "could not be inspected" in capsys.readouterr().err

    def test_a_local_dir_that_cannot_be_restricted_exits_2_before_the_command_runs(
        self, tmp_path: Path, monkeypatch, capsys,
    ) -> None:
        local_dir = tmp_path / "local"
        local_dir.mkdir()
        local_dir.chmod(0o775)

        def refuse_chmod(self, mode) -> None:
            raise PermissionError("not the owner")

        monkeypatch.setattr(Path, "chmod", refuse_chmod)
        monkeypatch.setattr(run_review_bench, "DEFAULT_LOCAL_DIR", local_dir)
        monkeypatch.setattr(run_review_bench, "cmd_mine_rounds", lambda args: pytest.fail("the command must not run"))

        assert run_review_bench.main(["mine-rounds"]) == 2
        assert "could not be restricted to its owner" in capsys.readouterr().err
