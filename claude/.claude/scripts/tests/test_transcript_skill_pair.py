"""Tests for transcript_analysis/skill_pair.py's cmd_skill_pair: ISO-week pairing rates, --exclude-projects,
--branches, and subagent-file followers."""
from transcript_analysis import skill_pair

from ._skill_helpers import _TS_FIXED, _skill_pair_args
from .conftest import _asst, _skill_use, _table_cols, _write_jsonl, _write_subagent_jsonl

# Boundary timestamps for test 7 (ISO week boundary Sun → Mon)
_TS_SUNDAY = "2026-05-17T23:59:59Z"   # ISO 2026-W20 (Sunday = last day of W20)
_TS_MONDAY = "2026-05-18T00:00:01Z"   # ISO 2026-W21 (Monday = first day of W21)


class TestSkillPair:
    def test_empty_jsonl_prints_no_data(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        assert "No data found." in capsys.readouterr().out

    def test_leader_only_session_counted_with_zero_followers(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "0"
        assert "0.0%" in cols["Pair%"]

    def test_leader_plus_main_follower_counted(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "1"
        assert "100.0%" in cols["Pair%"]

    def test_leader_plus_sidechain_only_follower(self, fake_projects, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"  # no main-thread follower
        assert cols["Side"] == "1"

    def test_both_main_and_sidechain_follower_counts_main_only(self, fake_projects, capsys):
        """Session with both main and sidechain follower hits counts in Main, NOT Side."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s3", "plan-review"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Main"] == "1"
        assert cols["Side"] == "0"  # sidechain-only requires no main hit

    def test_multiple_leader_hits_count_as_one_session(self, fake_projects, capsys):
        """Three leader invocations in one session → Lead=1, not 3."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
                _skill_use("s2", "plan-it"),
                _skill_use("s3", "plan-it"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_iso_week_boundary_sunday_vs_monday(self, fake_projects, capsys):
        """Leader on Sun 23:59:59 UTC → W20; leader on Mon 00:00:01 UTC → W21."""
        proj2 = fake_projects.parent / "-home-user-testrepo2"
        proj2.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess-sun.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_SUNDAY, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj2 / "sess-mon.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_MONDAY, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        data_lines = [ln for ln in out.splitlines() if ln.startswith("2026-W")]
        bins = [ln.split()[0] for ln in data_lines]
        assert "2026-W20" in bins
        assert "2026-W21" in bins
        assert bins != ["2026-W20"]  # both bins present as distinct rows

    def test_projects_glob_excludes_unmatched_project(self, fake_projects, capsys):
        """Only project dirs matching --projects are included."""
        proj_b = fake_projects.parent / "-home-user-other-repo"
        proj_b.mkdir(parents=True)
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_b / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        # Only match the first project dir
        skill_pair.cmd_skill_pair(_skill_pair_args(projects="-home-user-testrepo"))
        out = capsys.readouterr().out
        # Only 1 leader session (the included project)
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_exclude_projects_glob_omits_matching_dir(self, fake_projects, capsys):
        """Project dirs matching --exclude-projects are skipped even when also matching --projects=*."""
        proj_eval = fake_projects.parent / "-tmp-claude-eval-abc123"
        proj_normal = fake_projects.parent / "-home-user-normalrepo"
        proj_eval.mkdir(parents=True)
        proj_normal.mkdir(parents=True)
        # Normal project: 1 leader session
        _write_jsonl(proj_normal / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        # Eval project: 2 leader sessions — should be excluded
        _write_jsonl(proj_eval / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-it"),
            ]),
        ])
        _write_jsonl(proj_eval / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        # fake_projects itself has no sessions for this test; use exclude on the eval dir
        skill_pair.cmd_skill_pair(_skill_pair_args(exclude_projects="-tmp-claude-eval-*"))
        out = capsys.readouterr().out
        # Only 1 session from the normal project; eval sessions excluded
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"

    def test_branches_filter_excludes_other_branch(self, fake_projects, capsys):
        """With --branches=branch-a, only sessions on branch-a contribute."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
            _asst("claude-sonnet-4-6", branch="branch-a", ts=_TS_FIXED, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        _write_jsonl(fake_projects / "sess2.jsonl", [
            _asst("claude-sonnet-4-6", branch="branch-b", ts=_TS_FIXED, content=[
                _skill_use("s3", "plan-it"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args(branches="branch-a"))
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"  # branch-a only
        assert cols["Main"] == "1"  # follower on branch-a


class TestSkillPairSubagentFile:
    def test_follower_in_subagent_file_increments_sidechain_only(self, fake_projects, capsys):
        """Follower skill in a split subagent file (isSidechain=True) counts as Side, not Main."""
        session_id = "sess-pair"
        _write_jsonl(fake_projects / f"{session_id}.jsonl", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, content=[
                _skill_use("s1", "plan-it"),
            ]),
        ])
        _write_subagent_jsonl(fake_projects, session_id, "agent-1", [
            _asst("claude-sonnet-4-6", branch="main", ts=_TS_FIXED, sidechain=True, content=[
                _skill_use("s2", "plan-review"),
            ]),
        ])
        skill_pair.cmd_skill_pair(_skill_pair_args())
        out = capsys.readouterr().out
        cols = _table_cols(out, header_contains="Lead", row_contains="2026-W20")
        assert cols["Lead"] == "1"
        assert cols["Main"] == "0"
        assert cols["Side"] == "1"
