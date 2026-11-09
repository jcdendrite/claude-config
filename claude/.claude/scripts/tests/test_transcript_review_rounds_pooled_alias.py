"""review_rounds' --pooled refusal of a project directory reachable from more than one scan root, and the non-refusal of a
transcript file or session directory linked across scan roots."""
import re
from collections.abc import Sequence

import pytest
from transcript_analysis import review_rounds, scope

from ._review_round_corpus_helpers import _pooled_two_root_fixture, _review_round_cost_args
from .conftest import _agent_use, _priced, _skill_block, _user_msg, _write_jsonl, _write_subagent_dispatch


def _assert_refused_with_alias_count(
    captured, expected_count_clause: str, *, absent_names: Sequence[str],
) -> None:
    """The refusal exits with no stdout, states the count, names the declared-roots file as the remediation, ends with the
    documentation pointer, carries no traceback, and names none of `absent_names`."""
    out, err = captured
    assert out == ""
    assert expected_count_clause in err
    for absent_name in absent_names:
        assert absent_name not in err
    assert scope.TRANSCRIPT_CONFIG_DIRS_LABEL in err
    assert err.strip().endswith(review_rounds._POOLED_REFUSAL_DOC_POINTER.strip())
    assert "Traceback" not in err


class TestPooledCrossRootAliasRefusal:
    @pytest.mark.parametrize(
        "alias_target",
        [
            pytest.param("-home-user-repo-b", id="listed_project_dir_of_the_other_root"),
            pytest.param("-home-user-repo-b/nested", id="directory_inside_the_other_root_not_listed_there"),
        ],
    )
    def test_project_dir_linked_from_one_scan_root_into_another_is_refused_without_a_path(
        self, tmp_path, monkeypatch, capsys, alias_target,
    ):
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        (roots[1] / "-home-user-repo-b" / "nested").mkdir()
        alias_name = "-home-user-secret-alias-name"
        alias_target_name = alias_target.rsplit("/", 1)[-1]
        (roots[0] / alias_name).symlink_to(roots[1] / alias_target, target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        captured = capsys.readouterr()
        _assert_refused_with_alias_count(
            captured,
            "refuses a project directory reachable from more than one scan root (1 such directory)",
            absent_names=(alias_name, "-home-user-repo-b", alias_target_name),
        )
        assert str(tmp_path) not in captured.err

    def test_two_aliased_project_dirs_are_refused_with_a_plural_count(self, tmp_path, monkeypatch, capsys):
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        (roots[1] / "-home-user-repo-b-second").mkdir()
        (roots[0] / "-home-user-link-one").symlink_to(roots[1] / "-home-user-repo-b", target_is_directory=True)
        (roots[0] / "-home-user-link-two").symlink_to(roots[1] / "-home-user-repo-b-second", target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        _assert_refused_with_alias_count(
            capsys.readouterr(),
            "refuses a project directory reachable from more than one scan root (2 such directories)",
            absent_names=(
                "-home-user-link-one", "-home-user-link-two", "-home-user-repo-b", "-home-user-repo-b-second",
            ),
        )

    def test_project_dir_listed_under_two_scan_roots_through_links_is_refused(self, tmp_path, monkeypatch, capsys):
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        shared_proj = tmp_path / "outside-every-root" / "-home-user-repo-shared"
        shared_proj.mkdir(parents=True)
        for root in roots:
            (root / "-home-user-repo-shared-link").symlink_to(shared_proj, target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        captured = capsys.readouterr()
        # The target name is a substring of the link name, so it covers both.
        _assert_refused_with_alias_count(
            captured,
            "refuses a project directory reachable from more than one scan root (1 such directory)",
            absent_names=("-home-user-repo-shared",),
        )
        assert str(tmp_path) not in captured.err

    def test_non_pooled_run_over_a_cross_root_alias_completes_with_the_banner_and_no_refusal(
        self, tmp_path, monkeypatch, capsys,
    ):
        """The refusal is --pooled-only: a plain multi-root run over the same alias reads the directory once and prints
        the do-not-publish banner."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        (roots[0] / "-home-user-link-to-repo-b").symlink_to(roots[1] / "-home-user-repo-b", target_is_directory=True)

        review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=False))

        out, err = capsys.readouterr()
        assert scope._DO_NOT_PUBLISH_BANNER in out
        assert "Totals: 4 branches, 4 rounds" in out
        assert out.count("Totals:") == 1
        assert review_rounds._POOLED_REFUSAL_DOC_POINTER.strip() not in err


# The pooled fixture holds 4 rounds with 1 ready-for-review; the linked transcript adds 1 round, itself ready-for-review.
_BASELINE_READY_FOR_REVIEW_SHARE = "25.0%"  # 1 of 4 rounds
_LINKED_READY_FOR_REVIEW_SHARE = "40.0%"  # 2 of 5 rounds
_SUBAGENT_SPEND_SECTION = "  Share of branch spend"
_SUBAGENT_SPEND_ROW = "subagent dispatches only"
# The pooled fixture's four rounds cost $0.20 each; the added session adds a $0.20 round and a $1.00 dispatch.
_BASELINE_SUBAGENT_SPEND_SHARE = "50.0%"  # $1.00 of $2.00
_LINKED_SUBAGENT_SPEND_SHARE = "62.5%"  # $2.00 of $3.20, the linked session counted again


class TestPooledTranscriptFileLinkAcrossRoots:
    def test_a_transcript_file_linked_from_one_scan_root_to_another_completes_and_counts_the_round_twice(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Characterizes an accepted, documented limitation (docs/transcript-analysis.md, "Pooled mode"): the refusal
        covers project directories only, so a transcript file linked across roots is read under both."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        # show_withheld skips the dominance floor, which would blank every share to "(95% CI not computed".
        review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        baseline_ready_for_review_share = self._pooled_share(capsys.readouterr().out)
        (roots[0] / "-home-user-repo-a" / "sess-linked-b1.jsonl").symlink_to(
            roots[1] / "-home-user-repo-b" / "sess-b1.jsonl"
        )

        review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))

        linked_ready_for_review_share = self._pooled_share(capsys.readouterr().out)
        assert baseline_ready_for_review_share == _BASELINE_READY_FOR_REVIEW_SHARE
        assert linked_ready_for_review_share == _LINKED_READY_FOR_REVIEW_SHARE  # the linked transcript's round counts twice

    def test_a_transcript_and_its_session_directory_linked_across_scan_roots_count_the_subagent_work_under_both(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Characterizes an accepted, documented limitation (docs/transcript-analysis.md, "Pooled mode"): the refusal
        covers project directories only, so a session directory linked beside its transcript is read under both."""
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        listing_project = roots[0] / "-home-user-repo-a"
        session_project = roots[1] / "-home-user-repo-b"
        _write_jsonl(session_project / "sess-b3.jsonl", [
            _priced(  # $0.20, also spawns dispatch d1
                "claude-sonnet-5", input=100_000, branch="feat-b3", ts="2026-08-01T10:00:00.000Z",
                content=[_skill_block("s5", "ready-for-review"), _agent_use("d1", "staff-sdet")],
            ),
            _user_msg("thanks", branch="feat-b3", ts="2026-08-01T10:01:00.000Z"),
        ])
        _write_subagent_dispatch(
            session_project, "sess-b3", "agent-d1", "d1",
            [_priced("claude-sonnet-5", input=500_000, branch="feat-b3", ts="2026-08-01T10:00:30.000Z")],  # $1.00
        )
        review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))
        baseline_subagent_share = self._pooled_share(
            capsys.readouterr().out, _SUBAGENT_SPEND_SECTION, _SUBAGENT_SPEND_ROW
        )
        (listing_project / "sess-b3.jsonl").symlink_to(session_project / "sess-b3.jsonl")
        (listing_project / "sess-b3").symlink_to(session_project / "sess-b3", target_is_directory=True)

        review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True, show_withheld=True))

        linked_subagent_share = self._pooled_share(capsys.readouterr().out, _SUBAGENT_SPEND_SECTION, _SUBAGENT_SPEND_ROW)
        assert baseline_subagent_share == _BASELINE_SUBAGENT_SPEND_SHARE
        assert linked_subagent_share == _LINKED_SUBAGENT_SPEND_SHARE  # the linked session's dispatch counts twice

    @staticmethod
    def _pooled_share(
        pooled_output: str, section_header: str = "  Rounds by skill", row_label: str = "ready-for-review"
    ) -> str:
        lines = pooled_output.splitlines()
        assert section_header in lines, f"no {section_header.strip()!r} header in the pooled output:\n{pooled_output}"
        section_block = lines[lines.index(section_header) + 1:]
        share_pattern = re.compile(rf"^\s+{re.escape(row_label)}\s+(\d+(?:\.\d+)?%)")
        share_match = next((m for m in map(share_pattern.match, section_block) if m), None)
        assert share_match, f"no {row_label} share under {section_header.strip()!r}:\n{pooled_output}"
        return share_match.group(1)
