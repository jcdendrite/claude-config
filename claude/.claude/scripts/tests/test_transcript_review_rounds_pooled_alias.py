"""review_rounds' --pooled refusal of a project directory reachable from more than one declared root."""
import pytest
from transcript_analysis import review_rounds, scope

from ._review_round_corpus_helpers import _pooled_two_root_fixture, _review_round_cost_args


def _assert_refused_with_alias_count(captured, expected_count_clause: str) -> None:
    """The refusal exits with no stdout, states the count, names the declared-roots file as the remediation, ends with the
    documentation pointer, and carries no traceback."""
    out, err = captured
    assert out == ""
    assert expected_count_clause in err
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
    def test_project_dir_linked_from_one_declared_root_into_another_is_refused_without_a_path(
        self, tmp_path, monkeypatch, capsys, alias_target,
    ):
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        (roots[1] / "-home-user-repo-b" / "nested").mkdir()
        alias_name = "-home-user-secret-alias-name"
        (roots[0] / alias_name).symlink_to(roots[1] / alias_target, target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        captured = capsys.readouterr()
        _assert_refused_with_alias_count(
            captured, "refuses a project directory reachable from more than one declared root (1 such directory)"
        )
        assert alias_name not in captured.err
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
            capsys.readouterr(), "refuses a project directory reachable from more than one declared root (2 such directories)"
        )

    def test_project_dir_listed_under_two_declared_roots_through_links_is_refused(self, tmp_path, monkeypatch, capsys):
        roots = _pooled_two_root_fixture(tmp_path, monkeypatch)
        shared_proj = tmp_path / "outside-every-root" / "-home-user-repo-shared"
        shared_proj.mkdir(parents=True)
        for root in roots:
            (root / "-home-user-repo-shared-link").symlink_to(shared_proj, target_is_directory=True)

        with pytest.raises(SystemExit) as exc:
            review_rounds.cmd_review_round_cost(_review_round_cost_args(pooled=True))

        assert exc.value.code == 2
        captured = capsys.readouterr()
        _assert_refused_with_alias_count(
            captured, "refuses a project directory reachable from more than one declared root (1 such directory)"
        )
        assert str(tmp_path) not in captured.err
