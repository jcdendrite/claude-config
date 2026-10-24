"""Tests for transcript_analysis/pr_link.py's cmd_pr_link against a fake subprocess.run: PR lookup, the origin-derived
repo and the --repo override, gh failure diagnostics, comment-count failures, and the per-call gh timeout.
"""
import json
import subprocess

import pytest
from transcript_analysis import gh_cli, pr_link

from .conftest import _asst, _table_cols, _write_jsonl


class TestPrLink:
    def test_stubbed_gh_produces_row_with_pr_number(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-pr"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = ""
                returncode = 0

            r = R()
            if "list" in cmd:
                r.stdout = json.dumps([{"number": 77}])
            elif "issues" in " ".join(cmd):
                r.stdout = "alice\nbob\n"
            elif "pulls" in " ".join(cmd):
                r.stdout = "alice\n"
            return r

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {
            "repo": "owner/repo", "branches": "feat-pr", "author": "alice",
            "projects": "*", "this_repo": False,
        })()
        pr_link.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "77" in out
        assert "feat-pr" in out

    def test_branch_with_no_pr_shows_none(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="no-pr-branch"),
        ])

        def fake_run(cmd, *_, **__):
            class R:
                stdout = "[]"
                returncode = 0

            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = type("A", (), {
            "repo": "owner/repo", "branches": "no-pr-branch", "author": "",
            "projects": "*", "this_repo": False,
        })()
        pr_link.cmd_pr_link(args)
        out = capsys.readouterr().out
        assert "none" in out

    @staticmethod
    def _recording_run(origin_url: str, gh_stdout_by_verb: dict[str, str] | None = None):
        """subprocess.run double answering `git remote get-url origin` with
        origin_url and every gh call from gh_stdout_by_verb (keyed on
        "list" / "issues" / "pulls"); returns (fake_run, recorded_gh_argvs)."""
        recorded: list[list[str]] = []
        stdout_by_verb = gh_stdout_by_verb or {"list": json.dumps([{"number": 5}]), "issues": "", "pulls": ""}

        def fake_run(cmd, *_, **__):
            if cmd[:3] == ["git", "remote", "get-url"]:
                return subprocess.CompletedProcess(cmd, 0, origin_url + "\n", "")
            recorded.append(list(cmd))
            joined = " ".join(cmd)
            verb = "list" if "list" in cmd else "issues" if "issues" in joined else "pulls"
            return subprocess.CompletedProcess(cmd, 0, stdout_by_verb[verb], "")

        return fake_run, recorded

    @staticmethod
    def _pr_link_args(**overrides):
        fields = {"repo": None, "branches": "feat-y", "author": "", "projects": "*", "this_repo": False}
        fields.update(overrides)
        return type("A", (), fields)()

    def test_omitted_repo_derives_host_qualified_slug_from_origin(self, fake_projects, monkeypatch):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])
        fake_run, recorded = self._recording_run("git@ghe.example.com:Acme/Widget.git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args())

        pr_list_argv = next(argv for argv in recorded if "list" in argv)
        assert pr_list_argv[pr_list_argv.index("--repo") + 1] == "ghe.example.com/acme/widget"
        api_argvs = [argv for argv in recorded if argv[:2] == ["gh", "api"]]
        assert api_argvs
        for argv in api_argvs:
            assert argv[argv.index("--hostname") + 1] == "ghe.example.com"
            assert any(part.startswith("repos/acme/widget/") for part in argv)

    def test_omitted_repo_derives_host_qualified_slug_from_github_com_origin(self, fake_projects, monkeypatch):
        """github.com origin, not just a GHE host: _gh_host_qualified_repo
        host-qualifies unconditionally (see TestGhHostQualifiedRepo), so
        --repo carries "github.com/" the same way a GHE origin's host does."""
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])
        fake_run, recorded = self._recording_run("git@github.com:Acme/Widget.git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args())

        pr_list_argv = next(argv for argv in recorded if "list" in argv)
        assert pr_list_argv[pr_list_argv.index("--repo") + 1] == "github.com/acme/widget"
        api_argvs = [argv for argv in recorded if argv[:2] == ["gh", "api"]]
        assert api_argvs
        for argv in api_argvs:
            assert argv[argv.index("--hostname") + 1] == "github.com"
            assert any(part.startswith("repos/acme/widget/") for part in argv)

    def test_supplied_repo_wins_over_origin(self, fake_projects, monkeypatch):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])
        fake_run, recorded = self._recording_run("git@ghe.example.com:Acme/Widget.git")
        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="other/thing"))

        pr_list_argv = next(argv for argv in recorded if "list" in argv)
        assert pr_list_argv[pr_list_argv.index("--repo") + 1] == "other/thing"
        assert all("--hostname" not in argv for argv in recorded)
        api_argvs = [argv for argv in recorded if argv[:2] == ["gh", "api"]]
        assert api_argvs
        for argv in api_argvs:
            assert any(
                part.startswith("repos/other/thing/issues/") or part.startswith("repos/other/thing/pulls/")
                for part in argv
            )
            assert "--hostname" not in argv

    def test_no_usable_origin_names_repo_flag_as_escape_hatch(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            raise subprocess.CalledProcessError(128, cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            pr_link.cmd_pr_link(self._pr_link_args())
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("pr-link:")
        assert "--repo" in err

    def test_unrecognizable_origin_names_repo_flag_as_escape_hatch(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])
        fake_run, _recorded = self._recording_run("not a remote")
        monkeypatch.setattr(subprocess, "run", fake_run)

        with pytest.raises(SystemExit) as exc_info:
            pr_link.cmd_pr_link(self._pr_link_args())
        assert exc_info.value.code == 1
        err = capsys.readouterr().err
        assert err.startswith("pr-link:")
        assert "--repo" in err

    @pytest.mark.parametrize("stderr,expected_kind", [
        ("gh: Not logged into any GitHub hosts. Run gh auth login", "auth"),
        ("none of the git remotes configured for this repository correspond to the GH_HOST environment variable",
         "host_mismatch"),
        ("API rate limit exceeded for user", "rate_limit"),
        ("GraphQL: Could not resolve to a Repository with the name 'no/such-repo'.", "network or unrecognized"),
    ])
    def test_gh_failure_prints_classified_diagnostic_without_raw_stderr(
        self, fake_projects, monkeypatch, capsys, stderr, expected_kind,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            raise subprocess.CalledProcessError(1, cmd, output="", stderr=stderr)

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="no/such-repo"))
        captured = capsys.readouterr()
        assert "gh-err" in captured.out
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert diagnostic_lines == [f"pr-link: gh pr list failed for branch feat-y ({expected_kind})"]
        assert "no/such-repo" not in captured.err

    def test_gh_pr_list_unparseable_stdout_prints_unparseable_gh_output_diagnostic(
        self, fake_projects, monkeypatch, capsys,
    ):
        """gh pr list succeeding (check=True passes) but emitting non-JSON
        stdout -- the json.JSONDecodeError path, distinct from the
        CalledProcessError-shaped failures above."""
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            return subprocess.CompletedProcess(cmd, 0, "not json", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        assert "gh-err" in captured.out
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert diagnostic_lines == ["pr-link: gh pr list failed for branch feat-y (unparseable gh output)"]

    def test_missing_gh_binary_prints_gh_not_found_diagnostic(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(*_, **__):
            raise FileNotFoundError("gh")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        assert "gh-err" in captured.out
        assert "gh not found" in captured.err

    def test_gh_pr_list_timeout_prints_timeout_diagnostic(self, fake_projects, monkeypatch, capsys):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            raise subprocess.TimeoutExpired(cmd, gh_cli._GH_CALL_TIMEOUT_S)

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        assert "gh-err" in captured.out
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert diagnostic_lines == ["pr-link: gh pr list failed for branch feat-y (timeout)"]

    def test_first_branch_gh_failure_does_not_abort_remaining_branches(self, fake_projects, monkeypatch, capsys):
        """The per-branch loop degrades and continues: a gh pr list failure on
        the first branch must not prevent the second branch's own row."""
        _write_jsonl(fake_projects / "sess.jsonl", [
            _asst("claude-sonnet-4-6", branch="feat-fail"),
            _asst("claude-sonnet-4-6", branch="feat-ok"),
        ])

        def fake_run(cmd, *_, **__):
            if "feat-fail" in cmd:
                raise subprocess.CalledProcessError(1, cmd, output="", stderr="API rate limit exceeded")
            if "list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": 9}]), "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo", branches="feat-fail,feat-ok"))
        captured = capsys.readouterr()
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert diagnostic_lines == ["pr-link: gh pr list failed for branch feat-fail (rate_limit)"]
        cols = _table_cols(captured.out, header_contains="Branch", row_contains="feat-ok")
        assert cols["PR"] == "9"

    def test_comment_count_failure_prints_diagnostic_and_keeps_minus_one_cells(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            if "list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": 5}]), "")
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="API rate limit exceeded")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        cols = _table_cols(captured.out, header_contains="Branch", row_contains="feat-y")
        assert cols["PR"] == "5"
        assert cols["IssueCmt"] == "-1"
        assert cols["ReviewCmt"] == "-1"
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert len(diagnostic_lines) == 1
        assert "gh api comments" in diagnostic_lines[0]

    def test_pulls_comment_count_failure_after_issues_success_keeps_minus_one_cells(
        self, fake_projects, monkeypatch, capsys,
    ):
        """The issues call succeeding while only the pulls call fails still
        degrades both counters to -1 -- the try block's own shared except
        clause, not per-call recovery."""
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            if "list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": 5}]), "")
            if "issues" in " ".join(cmd):
                return subprocess.CompletedProcess(cmd, 0, "alice\n", "")
            raise subprocess.CalledProcessError(1, cmd, output="", stderr="API rate limit exceeded")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        cols = _table_cols(captured.out, header_contains="Branch", row_contains="feat-y")
        assert cols["PR"] == "5"
        assert cols["IssueCmt"] == "-1"
        assert cols["ReviewCmt"] == "-1"
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert len(diagnostic_lines) == 1
        assert "gh api comments" in diagnostic_lines[0]

    def test_comment_count_os_error_prints_diagnostic_and_keeps_minus_one_cells(
        self, fake_projects, monkeypatch, capsys,
    ):
        """A bare OSError at the comments call site (e.g. the gh binary
        vanishing mid-run) degrades to -1 the same as CalledProcessError or
        TimeoutExpired, rather than propagating uncaught."""
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            if "list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": 5}]), "")
            raise OSError("gh binary vanished mid-call")

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        cols = _table_cols(captured.out, header_contains="Branch", row_contains="feat-y")
        assert cols["PR"] == "5"
        assert cols["IssueCmt"] == "-1"
        assert cols["ReviewCmt"] == "-1"
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert len(diagnostic_lines) == 1
        assert "gh api comments" in diagnostic_lines[0]

    def test_comment_count_timeout_prints_diagnostic_and_keeps_minus_one_cells(
        self, fake_projects, monkeypatch, capsys,
    ):
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])

        def fake_run(cmd, *_, **__):
            if "list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, json.dumps([{"number": 5}]), "")
            raise subprocess.TimeoutExpired(cmd, gh_cli._GH_CALL_TIMEOUT_S)

        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo"))
        captured = capsys.readouterr()
        cols = _table_cols(captured.out, header_contains="Branch", row_contains="feat-y")
        assert cols["PR"] == "5"
        assert cols["IssueCmt"] == "-1"
        assert cols["ReviewCmt"] == "-1"
        diagnostic_lines = [ln for ln in captured.err.splitlines() if ln.startswith("pr-link:")]
        assert diagnostic_lines == ["pr-link: gh api comments failed for branch feat-y (timeout)"]

    @staticmethod
    def _kwarg_recording_run(pr_list_stdout: str, issues_stdout: str, pulls_stdout: str):
        """subprocess.run spy recording each call's kwargs, keyed on gh verb
        ("list" / "issues" / "pulls"), so a test can assert timeout= was
        passed at every call site."""
        recorded: dict[str, dict] = {}

        def fake_run(cmd, *_, **kwargs):
            joined = " ".join(cmd)
            verb = "list" if "list" in cmd else "issues" if "issues" in joined else "pulls"
            recorded[verb] = kwargs
            stdout = {"list": pr_list_stdout, "issues": issues_stdout, "pulls": pulls_stdout}[verb]
            return subprocess.CompletedProcess(cmd, 0, stdout, "")

        return fake_run, recorded

    def test_every_gh_call_passes_configured_timeout(self, fake_projects, monkeypatch):
        """Guards the timeout= kwarg on all three subprocess.run call sites
        (gh pr list, gh api issues comments, gh api pulls comments)."""
        _write_jsonl(fake_projects / "sess.jsonl", [_asst("claude-sonnet-4-6", branch="feat-y")])
        fake_run, recorded = self._kwarg_recording_run(
            pr_list_stdout=json.dumps([{"number": 5}]), issues_stdout="alice\n", pulls_stdout="alice\n",
        )
        monkeypatch.setattr(subprocess, "run", fake_run)

        pr_link.cmd_pr_link(self._pr_link_args(repo="owner/repo", author="alice"))

        assert set(recorded) == {"list", "issues", "pulls"}
        for kwargs in recorded.values():
            assert kwargs["timeout"] == gh_cli._GH_CALL_TIMEOUT_S
