"""scope.py's root attribution and cross-root alias detection, over plain roots and over project directories and
transcripts reached through a symlink."""
import os
from datetime import date
from pathlib import Path

import pytest
from transcript_analysis import cost, scope

from ._cost_report_helpers import _extract_account_totals
from .conftest import _cost_args, _priced, _write_cost_root, _write_jsonl

_ONE_MILLION_INPUT_TOKENS = [_priced("claude-sonnet-5", input=1_000_000)]
_TWO_MILLION_INPUT_TOKENS = [_priced("claude-sonnet-5", input=2_000_000)]
_THREE_ROOT_INDEXES = [pytest.param(0, id="first-root"), pytest.param(1, id="middle-root"), pytest.param(2, id="last-root")]


def _three_roots(tmp_path):
    roots = [tmp_path / name for name in ("acct-a", "acct-b", "acct-c")]
    for root in roots:
        root.mkdir()
    return roots


class TestRootIndexForPath:
    @pytest.mark.parametrize("listed_index", _THREE_ROOT_INDEXES)
    def test_session_in_a_symlinked_project_dir_is_attributed_to_the_root_it_is_listed_under(self, tmp_path, listed_index):
        roots = _three_roots(tmp_path)
        outside = tmp_path / "outside" / "-repo-relocated"
        outside.mkdir(parents=True)
        _write_jsonl(outside / "sess.jsonl", _ONE_MILLION_INPUT_TOKENS)
        (roots[listed_index] / "-repo-relocated").symlink_to(outside)

        listed = roots[listed_index] / "-repo-relocated" / "sess.jsonl"

        assert scope._root_index_for_path(listed, [root.resolve() for root in roots]) == listed_index

    @pytest.mark.parametrize("listed_index", _THREE_ROOT_INDEXES)
    def test_transcript_symlinked_outside_every_root_is_attributed_to_the_root_it_is_listed_under(self, tmp_path, listed_index):
        roots = _three_roots(tmp_path)
        (roots[listed_index] / "-repo-main").mkdir()
        outside_transcript = tmp_path / "outside" / "sess-outside.jsonl"
        outside_transcript.parent.mkdir()
        _write_jsonl(outside_transcript, _ONE_MILLION_INPUT_TOKENS)
        listed = roots[listed_index] / "-repo-main" / "link.jsonl"
        listed.symlink_to(outside_transcript)

        assert scope._root_index_for_path(listed, [root.resolve() for root in roots]) == listed_index

    def test_root_index_lookup_failure_omits_raw_path(self, tmp_path):
        """A path whose grandparent is no listed root raises an AssertionError that carries neither the path's
        directory names nor its filename: main() has no top-level exception handler, so the message reaches stderr."""
        root = tmp_path / "acct-a"
        root.mkdir()
        unlisted = tmp_path / "external-unrelated-secret-clientname" / "-home-user-escaped" / "sess-escaped.jsonl"

        with pytest.raises(AssertionError, match=r"a session path \(hash [0-9a-f]{12}\) matched no known scan root") as exc_info:
            scope._root_index_for_path(unlisted, [root.resolve()])
        assert "external-unrelated-secret-clientname" not in str(exc_info.value)
        assert "-home-user-escaped" not in str(exc_info.value)
        assert "sess-escaped" not in str(exc_info.value)


class TestCostWithSymlinkedProjectDirs:
    def test_multi_root_redacted_cost_completes_when_a_project_dir_is_symlinked_into_another_root(self, tmp_path, capsys):
        """Root A lists root B's project directory under a different name, so the redact map keys the session by A's name
        while the session's real path lies under B. The linked session's tokens land under A's account (account-1)."""
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _TWO_MILLION_INPUT_TOKENS)
        (root_a / "-home-user-repo-alias").symlink_to(root_b / "-home-user-repo-b")

        cost._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])

        account_totals = _extract_account_totals(capsys.readouterr().out)
        assert account_totals[1] == pytest.approx(6.00)  # 3M input tokens at $2.00 per million
        assert account_totals[2] == 0.0

    def test_multi_root_redacted_cost_completes_when_a_project_dir_is_symlinked_outside_every_root(self, tmp_path, capsys):
        root_a = _write_cost_root(tmp_path, "acct-a", "-home-user-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        external = tmp_path / "external"
        external.mkdir()
        _write_jsonl(external / "sess-escaped.jsonl", _TWO_MILLION_INPUT_TOKENS)
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        (root_b / "-home-user-escaped").symlink_to(external)

        cost._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])

        account_totals = _extract_account_totals(capsys.readouterr().out)
        assert account_totals[1] == pytest.approx(2.00)  # 1M input tokens
        assert account_totals[2] == pytest.approx(4.00)  # 2M input tokens

    def test_project_dir_listed_under_two_roots_is_scanned_under_each_root_but_priced_once(self, tmp_path, capsys):
        """The per-root scan diagnostic is not deduplicated across roots, unlike the per-account totals."""
        root_a = tmp_path / "acct-a"
        root_a.mkdir()
        root_b = _write_cost_root(tmp_path, "acct-b", "-home-user-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_a / "-home-user-repo-alias").symlink_to(root_b / "-home-user-repo-b")

        cost._cost_report(_cost_args(), date(2026, 8, 2), roots=[root_a, root_b])

        out = capsys.readouterr().out
        assert "cost: account-1: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert "cost: account-2: scanned 1 transcripts, 0 skipped (unreadable)" in out
        assert sum(_extract_account_totals(out).values()) == pytest.approx(2.00)  # 1M input tokens, once


_ITERATORS = [
    pytest.param(lambda roots, slugs: scope._iter_glob_scoped_sessions(roots, "*", False), id="glob-scoped"),
    pytest.param(lambda roots, slugs: scope._iter_scoped_sessions(slugs, False, roots), id="slug-scoped"),
]


def _attribution_by_transcript_name(iterate, roots, slugs) -> dict[str, tuple[Path, int]]:
    """For each yielded transcript name, its lexical grandparent and the root index _root_index_for_path attributes it to."""
    resolved_roots = [root.resolve() for root in roots]
    attributed = {}
    for jsonl, _records in iterate(roots, slugs):
        attributed[jsonl.name] = (jsonl.parent.parent, scope._root_index_for_path(jsonl, resolved_roots))
    return attributed


@pytest.mark.parametrize("iterate", _ITERATORS)
class TestIteratorsYieldPathsUnderTheirListedRoot:
    """_root_index_for_path relies on each multi-root iterator yielding root/name/file.jsonl with the unresolved root."""

    def test_symlinked_project_dir_and_symlinked_transcript_are_yielded_under_the_root_listing_them(self, tmp_path, iterate):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = tmp_path / "acct-b"
        root_b.mkdir()
        outside_project = tmp_path / "outside" / "-repo-linked"
        outside_project.mkdir(parents=True)
        _write_jsonl(outside_project / "sess-linked-dir.jsonl", _ONE_MILLION_INPUT_TOKENS)
        (root_b / "-repo-linked").symlink_to(outside_project)
        outside_transcript = tmp_path / "outside" / "sess-outside.jsonl"
        _write_jsonl(outside_transcript, _ONE_MILLION_INPUT_TOKENS)
        (root_b / "-repo-real").mkdir()
        (root_b / "-repo-real" / "sess-linked-file.jsonl").symlink_to(outside_transcript)

        attributed = _attribution_by_transcript_name(
            iterate, [root_a, root_b], ["-repo-a", "-repo-linked", "-repo-real"],
        )

        assert attributed == {
            "sess-a.jsonl": (root_a, 0),
            "sess-linked-dir.jsonl": (root_b, 1),
            "sess-linked-file.jsonl": (root_b, 1),
        }

    def test_session_under_a_root_reached_through_a_symlinked_path_is_attributed_to_the_listing_root(self, tmp_path, iterate):
        real_a = _write_cost_root(tmp_path, "real-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        link_a = tmp_path / "link-a"
        link_a.symlink_to(real_a, target_is_directory=True)
        root_b = _write_cost_root(tmp_path, "plain-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)

        attributed = _attribution_by_transcript_name(iterate, [link_a, root_b], ["-repo-a", "-repo-b"])

        assert attributed == {"sess-a.jsonl": (link_a, 0), "sess-b.jsonl": (root_b, 1)}

    @pytest.mark.parametrize("scan_order", [pytest.param((0, 1), id="a-first"), pytest.param((1, 0), id="b-first")])
    def test_project_dir_listed_under_two_roots_is_attributed_to_the_root_scanned_first(self, tmp_path, iterate, scan_order):
        roots = [
            _write_cost_root(tmp_path, name, "-repo-own", f"sess-{name}", _ONE_MILLION_INPUT_TOKENS)
            for name in ("acct-a", "acct-b")
        ]
        shared = tmp_path / "outside" / "-repo-shared"
        shared.mkdir(parents=True)
        _write_jsonl(shared / "sess-shared.jsonl", _ONE_MILLION_INPUT_TOKENS)
        for root in roots:
            (root / "-repo-shared").symlink_to(shared, target_is_directory=True)
        scanned = [roots[position] for position in scan_order]

        attributed = _attribution_by_transcript_name(iterate, scanned, ["-repo-own", "-repo-shared"])

        assert attributed["sess-shared.jsonl"] == (scanned[0], 0)

    def test_session_under_a_root_nested_in_another_declared_root_is_attributed_to_the_nested_root(self, tmp_path, iterate):
        outer = _write_cost_root(tmp_path, "outer", "-repo-outer", "sess-outer", _ONE_MILLION_INPUT_TOKENS)
        inner = _write_cost_root(outer, "inner", "-repo-inner", "sess-inner", _ONE_MILLION_INPUT_TOKENS)

        attributed = _attribution_by_transcript_name(iterate, [outer, inner], ["-repo-outer", "-repo-inner"])

        assert attributed == {"sess-outer.jsonl": (outer, 0), "sess-inner.jsonl": (inner, 1)}


class TestCrossRootAliasProjectDirCount:
    def test_roots_with_only_their_own_real_project_dirs_have_no_alias(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 0

    def test_project_dir_linked_into_another_root_counts_once_with_its_target_also_listed(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_a / "-repo-alias").symlink_to(root_b / "-repo-b", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 1

    def test_link_into_a_root_whose_name_extends_the_listing_roots_name_counts(self, tmp_path):
        """`acct-b` is a string-prefix extension of `acct` but not inside it, so a containment check by string prefix
        would read the link target as inside the listing root. The target is a directory `acct-b` does not list, so only the
        containment check can find the alias."""
        root_acct = _write_cost_root(tmp_path, "acct", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_acct_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_acct_b / "-repo-b" / "nested").mkdir()
        (root_acct / "-repo-alias").symlink_to(root_acct_b / "-repo-b" / "nested", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_acct, root_acct_b]) == 1

    def test_link_into_an_undeclared_directory_whose_name_extends_another_roots_name_is_not_an_alias(self, tmp_path):
        """`acct-b-archive` is a string-prefix extension of the other root `acct-b` but not inside it, so a containment
        check of the other root by string prefix would count the target. `acct-a` does not prefix the target, so the
        listing root's own check cannot mask that."""
        root_acct_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_acct_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        archived_project_dir = tmp_path / "acct-b-archive" / "-repo-archived"
        archived_project_dir.mkdir(parents=True)
        (root_acct_a / "-repo-alias").symlink_to(archived_project_dir, target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_acct_a, root_acct_b]) == 0

    def test_link_to_a_directory_inside_another_root_that_root_does_not_list_counts(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_b / "-repo-b" / "nested").mkdir()
        (root_a / "-repo-alias").symlink_to(root_b / "-repo-b" / "nested", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 1

    def test_link_into_a_directory_inside_a_root_reached_through_a_symlinked_path_counts(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        real_b = _write_cost_root(tmp_path, "real-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        link_b = tmp_path / "link-b"
        link_b.symlink_to(real_b, target_is_directory=True)
        (real_b / "-repo-b" / "nested").mkdir()
        (root_a / "-repo-alias").symlink_to(real_b / "-repo-b" / "nested", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, link_b]) == 1

    def test_two_distinct_aliased_directories_count_as_two(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_b / "-repo-b-second").mkdir()
        (root_a / "-repo-alias-one").symlink_to(root_b / "-repo-b", target_is_directory=True)
        (root_a / "-repo-alias-two").symlink_to(root_b / "-repo-b-second", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 2

    def test_one_directory_linked_from_two_roots_counts_once_in_either_root_order(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        shared = tmp_path / "outside" / "-repo-shared"
        shared.mkdir(parents=True)
        (root_a / "-repo-link-a").symlink_to(shared, target_is_directory=True)
        (root_b / "-repo-link-b").symlink_to(shared, target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 1
        assert scope._cross_root_alias_project_dir_count([root_b, root_a]) == 1

    def test_links_outside_every_root_and_two_links_to_one_directory_in_one_root_are_not_aliases(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        outside = tmp_path / "outside" / "-repo-elsewhere"
        outside.mkdir(parents=True)
        (root_b / "-repo-link-one").symlink_to(outside, target_is_directory=True)
        (root_b / "-repo-link-two").symlink_to(outside, target_is_directory=True)
        (root_a / "-repo-a-alias").symlink_to(root_a / "-repo-a", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 0

    def test_a_root_nested_inside_another_is_not_an_alias(self, tmp_path):
        outer = _write_cost_root(tmp_path, "outer", "-repo-outer", "sess-outer", _ONE_MILLION_INPUT_TOKENS)
        inner = _write_cost_root(outer, "inner", "-repo-inner", "sess-inner", _ONE_MILLION_INPUT_TOKENS)

        assert scope._cross_root_alias_project_dir_count([outer, inner]) == 0

    def test_a_missing_root_is_skipped(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)

        assert scope._cross_root_alias_project_dir_count([root_a, tmp_path / "missing"]) == 0

    def test_a_dangling_or_looping_project_dir_link_is_skipped(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_a / "-repo-dangling").symlink_to(tmp_path / "does-not-exist", target_is_directory=True)
        (root_b / "-repo-loop").symlink_to(root_b / "-repo-loop")

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 0

    def test_a_dangling_link_beside_a_real_alias_does_not_hide_the_alias(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_a / "-repo-dangling").symlink_to(tmp_path / "does-not-exist", target_is_directory=True)
        (root_a / "-repo-alias").symlink_to(root_b / "-repo-b", target_is_directory=True)
        (root_b / "-repo-loop").symlink_to(root_b / "-repo-loop")

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 1

    @pytest.mark.parametrize("root_order", [pytest.param((0, 1, 2), id="a-b-c"), pytest.param((2, 1, 0), id="c-b-a")])
    def test_link_from_a_third_root_into_an_unlisted_subdirectory_of_the_middle_root_counts_in_either_root_order(
        self, tmp_path, root_order
    ):
        roots = [
            _write_cost_root(tmp_path, name, f"-repo-{name}", f"sess-{name}", _ONE_MILLION_INPUT_TOKENS)
            for name in ("acct-a", "acct-b", "acct-c")
        ]
        (roots[1] / "-repo-acct-b" / "nested").mkdir()
        (roots[2] / "-repo-alias").symlink_to(roots[1] / "-repo-acct-b" / "nested", target_is_directory=True)

        assert scope._cross_root_alias_project_dir_count([roots[idx] for idx in root_order]) == 1

    def test_a_symlink_to_a_regular_file_in_another_root_is_not_a_project_dir_alias(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        (root_a / "link.jsonl").symlink_to(root_b / "-repo-b" / "sess-b.jsonl")

        assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 0

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permission bits")
    def test_a_root_that_cannot_be_searched_is_skipped_without_raising(self, tmp_path):
        root_a = _write_cost_root(tmp_path, "acct-a", "-repo-a", "sess-a", _ONE_MILLION_INPUT_TOKENS)
        root_b = _write_cost_root(tmp_path, "acct-b", "-repo-b", "sess-b", _ONE_MILLION_INPUT_TOKENS)
        root_a.chmod(0o444)
        try:
            assert scope._cross_root_alias_project_dir_count([root_a, root_b]) == 0
        finally:
            root_a.chmod(0o755)
