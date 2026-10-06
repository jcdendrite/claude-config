"""Tests for evals/review_bench's defect schema, miners, and confirmation
CLI. Offline throughout: mine_szz and mine_review_rounds
fixtures are real tmp-path git repos (a local bare repo standing in for
`origin` where a PR-head fetch is exercised) and synthetic transcript
JSONL. mine_pr_comments runs against real tmp-path git repos too, with a fake
`gh` that derives each answer from its argv, and no network git transport.
No test launches `claude`.
"""
from __future__ import annotations

import io
import json
import os
import re
import signal
import subprocess
import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
import run_review_bench
from review_bench import defects, mine_pr_comments, mine_review_rounds, mine_szz
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


# Engineer-side git settings that change the text `git diff` prints.
ENGINEER_GIT_SETUPS = (
    "color-ui-always", "diff-external-config", "external-diff-env", "diff-opts-env", "xdg-default-attributes-file",
)
# The subset that also changes `git show`. `git show` ignores `diff.external` and `GIT_EXTERNAL_DIFF` without `--ext-diff`.
ENGINEER_GIT_SETUPS_THAT_CHANGE_GIT_SHOW = ("color-ui-always", "diff-opts-env", "xdg-default-attributes-file")


def apply_engineer_git_setup(setup: str, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Puts one of `ENGINEER_GIT_SETUPS` into the process environment, and its files under `root`. A plain
    `git diff` of a multi-line `.py` edit differs from an unaffected run under every one of them."""
    external_diff = root / "external-diff.sh"
    external_diff.write_text("#!/bin/sh\necho EXTERNAL-DIFF-OUTPUT\n")
    external_diff.chmod(0o755)
    global_config = root / "engineer-gitconfig"
    if setup == "color-ui-always":
        global_config.write_text("[color]\n\tui = always\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    elif setup == "diff-external-config":
        global_config.write_text(f"[diff]\n\texternal = {external_diff}\n")
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    elif setup == "external-diff-env":
        monkeypatch.setenv("GIT_EXTERNAL_DIFF", str(external_diff))
    elif setup == "diff-opts-env":
        monkeypatch.setenv("GIT_DIFF_OPTS", "--unified=0")
    elif setup == "xdg-default-attributes-file":
        # git reads `$XDG_CONFIG_HOME/git/attributes` when `core.attributesFile` is unset, whatever the global config says.
        attributes_dir = root / "xdg-config" / "git"
        attributes_dir.mkdir(parents=True)
        (attributes_dir / "attributes").write_text("*.py -diff\n")
        monkeypatch.setenv("XDG_CONFIG_HOME", str(root / "xdg-config"))
    else:
        raise ValueError(f"unknown engineer git setup {setup!r}")


# Settings in the source repository's own `.git/config` and `.git/info/attributes`, which no environment isolation removes.
SOURCE_REPO_DIFF_SETTINGS = ("color-ui-always", "diff-external-config", "textconv-driver")
# The subset that also changes `git show`. `git show` ignores `diff.external` without `--ext-diff`.
SOURCE_REPO_DIFF_SETTINGS_THAT_CHANGE_GIT_SHOW = ("color-ui-always", "textconv-driver")


def apply_source_repo_diff_setting(setting: str, repo: Path) -> None:
    """Puts one of `SOURCE_REPO_DIFF_SETTINGS` into `repo`'s own git config and attributes. A plain `git diff` of
    a multi-line `.py` edit differs from an unaffected run under every one of them."""
    helper_script = repo.parent / f"{setting}.sh"
    if setting == "color-ui-always":
        _git(repo, "config", "color.ui", "always")
    elif setting == "diff-external-config":
        helper_script.write_text("#!/bin/sh\necho EXTERNAL-DIFF-OUTPUT\n")
        helper_script.chmod(0o755)
        _git(repo, "config", "diff.external", str(helper_script))
    elif setting == "textconv-driver":
        helper_script.write_text('#!/bin/sh\ntr a-z A-Z < "$1"\n')
        helper_script.chmod(0o755)
        _git(repo, "config", "diff.shout.textconv", str(helper_script))
        (repo / ".git" / "info").mkdir(exist_ok=True)
        (repo / ".git" / "info" / "attributes").write_text("*.py diff=shout\n")
    else:
        raise ValueError(f"unknown source repo diff setting {setting!r}")


def commit_two_versions_of_a_multi_line_python_file(repo: Path) -> tuple[str, str]:
    """(base, head): head edits the middle line of a five-line file, so a diff holds context lines."""
    _write(repo, "app.py", "a = 1\nb = 2\nc = 3\nd = 4\ne = 5\n")
    base_sha = _commit(repo, "add app")
    _write(repo, "app.py", "a = 1\nb = 2\nc = 30\nd = 4\ne = 5\n")
    return base_sha, _commit(repo, "edit app")


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
        # Every miner records the candidate's path, and `confirm` copies it onto the ConfirmedDefect.
        evidence={"path": "app.py"},
    )
    kwargs.update(overrides)
    return kwargs


def _confirmed_defect_data(**overrides) -> dict:
    data = {
        "id": "c1", "source": "szz", "lens": "staff-backend-engineer",
        "base_commit": _SHA_A, "head_commit": _SHA_B, "fix_commit": _SHA_C,
        "fix_date": "2026-01-01T00:00:00+00:00", "description": "fixed a bug",
        "path": "app.py", "file_is_markdown": False,
    }
    data.update(overrides)
    return data


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

    @pytest.mark.parametrize("required_key", ["path", "file_is_markdown"])
    def test_confirmed_defect_without_a_required_key_is_rejected_not_defaulted(self, required_key):
        """A record lacking `path` or `file_is_markdown` must fail to load, so a
        `.get(key, default)` implementation would read it as a path-less or
        non-markdown defect and fail this test."""
        data = _confirmed_defect_data()
        del data[required_key]
        with pytest.raises(ValueError, match=f"missing.*{required_key}"):
            ConfirmedDefect.from_dict(data)

    @pytest.mark.parametrize("bad_path", ["", None, 7, ["app.py"]])
    def test_confirmed_defect_with_an_empty_or_non_string_path_is_rejected(self, bad_path):
        with pytest.raises(ValueError, match="path"):
            ConfirmedDefect.from_dict(_confirmed_defect_data(path=bad_path))

    @pytest.mark.parametrize("not_a_bool", ["false", "true", "", 0, 1, None])
    def test_confirmed_defect_with_a_non_bool_file_is_markdown_is_rejected(self, not_a_bool):
        """A JSON string like "false" is truthy, so only a real bool is accepted."""
        with pytest.raises(ValueError, match="file_is_markdown"):
            ConfirmedDefect.from_dict(_confirmed_defect_data(file_is_markdown=not_a_bool))

    @pytest.mark.parametrize("source", ["szz", "review-round", "pr-comment"])
    def test_every_known_source_is_accepted_on_a_confirmed_defect(self, source):
        assert ConfirmedDefect.from_dict(_confirmed_defect_data(source=source)).source == source

    def test_known_sources_is_exactly_the_three_source_names(self):
        assert sorted(defects.KNOWN_SOURCES) == ["pr-comment", "review-round", "szz"]


class TestIsMarkdownPath:
    @pytest.mark.parametrize("path", ["docs/x.md", "x.md", "x.MD", "docs/Guide.Md", "notes.markdown", "a/b/NOTES.MARKDOWN"])
    def test_markdown_suffixes_match_case_insensitively(self, path):
        assert defects.is_markdown_path(path) is True

    @pytest.mark.parametrize("path", ["docs.md/x.py", "x.mdx", "README", "x.md.bak", "md", "docs/.md", "app.py", ""])
    def test_non_markdown_paths_do_not_match(self, path):
        assert defects.is_markdown_path(path) is False

    def test_guess_lens_routes_an_uppercase_md_path_to_the_markdown_lens(self):
        assert defects.guess_lens("docs/README.MD") == "comment-discipline-reviewer"


_MARKDOWN_AGREEMENT_PATHS = [
    "x.MD", "x.markdown", "docs/a.md", "notes.markdown", "a.mdx", "a.md.bak", "md", "docs.md/x.py", "README",
]


class TestMarkdownDecisionAgreesAcrossMiners:
    """Each miner's own markdown decision, driven through its own code path,
    must equal `is_markdown_path`'s."""

    @pytest.mark.parametrize("path", _MARKDOWN_AGREEMENT_PATHS)
    def test_szz_filter_skips_exactly_the_markdown_paths(self, tmp_path, path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "seed.txt", "unrelated\n")
        _commit(repo, "seed")  # root commit: holds none of the lines under test
        _write(repo, path, "keep = 1\nbad_call()\n")
        _commit(repo, "add file")
        _write(repo, path, "keep = 1\n")
        fix_sha = _commit(repo, "fix removal")
        _set_origin_main(repo, fix_sha)

        mined_paths = [candidate.evidence["path"] for candidate in mine_szz.mine(repo, base_ref="origin/main")]
        assert mined_paths == ([] if defects.is_markdown_path(path) else [path])

    @pytest.mark.parametrize("path", _MARKDOWN_AGREEMENT_PATHS)
    def test_review_round_candidate_flags_exactly_the_markdown_paths(self, tmp_path, monkeypatch, path):
        cited_path = f"/repo/{path}"
        project_dir = tmp_path / "projects" / "test-slug"
        project_dir.mkdir(parents=True)
        dispatch_id = "toolu_agent1"
        _write_jsonl(project_dir / "sess-1.jsonl", [
            _assistant(branch="feat", ts="2026-01-01T00:00:00Z", content=[_skill_use("s1", "code-review")]),
            _assistant(branch="feat", ts="2026-01-01T00:01:00Z", content=[_read_use("r1", cited_path)]),
            _user("looks fine", branch="feat", ts="2026-01-01T00:02:00Z"),
            _assistant(branch="feat", ts="2026-01-02T00:00:00Z", content=[_skill_use("s2", "code-review")]),
            _assistant(
                branch="feat", ts="2026-01-02T00:01:00Z", content=[_agent_use(dispatch_id, "staff-backend-engineer")],
            ),
            _user("thanks", branch="feat", ts="2026-01-02T00:02:00Z"),
        ])
        finding = f"Reviewer finding: {cited_path}:12 has a bug in the loop."
        _write_subagent_dispatch(
            project_dir, "sess-1", "agent-1", dispatch_id,
            [_assistant(ts="2026-01-02T00:01:30Z", cwd="/repo", content=[{"type": "text", "text": finding}])],
        )
        resolution = mine_review_rounds._CommitResolution(
            ref_status="local-branch", base_commit=_SHA_A, head_commit=_SHA_B,
            fix_commit=_SHA_C, fix_date="2026-01-01T00:00:00+00:00",
        )
        monkeypatch.setattr(mine_review_rounds.scope, "_repo_scoped_project_slugs", lambda label: ["test-slug"])
        monkeypatch.setattr(mine_review_rounds, "resolve_pr_number", lambda *a, **k: None)
        monkeypatch.setattr(
            mine_review_rounds, "resolve_branch_ref", lambda repo_dir, branch, pr_number: ("local-branch", "refs/heads/feat"),
        )
        monkeypatch.setattr(
            mine_review_rounds, "open_branch_git",
            lambda repo_dir, branch_ref: mine_review_rounds._BranchGit(
                ref_status=branch_ref[0], ref=branch_ref[1], merge_base=_SHA_A, touched_paths=frozenset({path}),
            ),
        )
        monkeypatch.setattr(mine_review_rounds, "resolve_defect_commits", lambda repo_dir, *, path, after_ts, branch: resolution)

        candidates = mine_review_rounds.mine(tmp_path, roots=(tmp_path / "projects",))

        assert [candidate.file_is_markdown for candidate in candidates] == [defects.is_markdown_path(path)]

    @pytest.mark.parametrize("path", _MARKDOWN_AGREEMENT_PATHS)
    def test_pr_comment_candidate_flags_exactly_the_markdown_paths(self, tmp_path, path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path, path=path)
        thread = [_pr_review_comment(introducing_sha, path=path), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        (candidate,) = _mine_pr_comments(repo, thread)[0]

        assert candidate.file_is_markdown is defects.is_markdown_path(path)
        assert (candidate.lens == "comment-discipline-reviewer") is defects.is_markdown_path(path)

    @pytest.mark.parametrize("path", _MARKDOWN_AGREEMENT_PATHS)
    def test_guess_lens_routes_exactly_the_markdown_paths_to_the_markdown_lens(self, path):
        assert (defects.guess_lens(path) == "comment-discipline-reviewer") is defects.is_markdown_path(path)


class TestNoInlineMarkdownSuffixCheck:
    """A tripwire, not a behavior test: it fails on the source text that lets a
    miner drift from `is_markdown_path`, and says nothing about whether the
    miners agree (TestMarkdownDecisionAgreesAcrossMiners covers that)."""

    _INLINE_MARKDOWN_SUFFIX_CHECK_RE = re.compile(r"""endswith\(.*["']\.(?:md|markdown)["']""", re.IGNORECASE)

    def test_no_review_bench_module_checks_a_markdown_suffix_inline(self):
        offending_lines = [
            f"{source_file.name}:{line_number}: {line.strip()}"
            for source_file in sorted(Path(defects.__file__).parent.glob("*.py"))
            for line_number, line in enumerate(source_file.read_text().splitlines(), start=1)
            if self._INLINE_MARKDOWN_SUFFIX_CHECK_RE.search(line)
        ]
        assert not offending_lines, (
            "TRIPWIRE: an inline markdown suffix check bypasses defects.is_markdown_path "
            "(the one markdown predicate); call it instead:\n" + "\n".join(offending_lines)
        )

    @pytest.mark.parametrize("source_line", [
        'return path.endswith(".md")',
        "return path.lower().endswith('.markdown')",
        'return path.endswith((".MD", ".markdown"))',
    ])
    def test_the_tripwire_pattern_matches_the_shapes_it_guards(self, source_line):
        assert self._INLINE_MARKDOWN_SUFFIX_CHECK_RE.search(source_line)


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
            path="app.py", file_is_markdown=False,
        )
        defects.save_confirmed_defects(path, [defect])
        assert defects.load_confirmed_defects(path) == [defect]

    def test_round_trips_path_and_file_is_markdown_through_the_json_file(self, tmp_path):
        path = tmp_path / "defects.json"
        defect = ConfirmedDefect.from_dict(_confirmed_defect_data(
            source="pr-comment", path="docs/guide.md", file_is_markdown=True,
        ))
        defects.save_confirmed_defects(path, [defect])
        written = json.loads(path.read_text())
        assert written[0]["path"] == "docs/guide.md"
        assert written[0]["file_is_markdown"] is True
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

    @pytest.mark.parametrize("setup", ENGINEER_GIT_SETUPS_THAT_CHANGE_GIT_SHOW)
    def test_the_text_does_not_depend_on_the_engineers_git_setup(self, tmp_path, monkeypatch, setup):
        repo = _init_repo(tmp_path / "repo")
        introducing_sha, fix_sha = commit_two_versions_of_a_multi_line_python_file(repo)
        unaffected_text = defects.public_git_text(repo, introducing_sha, fix_sha)
        unaffected_plain_show = _git(repo, "show", fix_sha)

        apply_engineer_git_setup(setup, tmp_path, monkeypatch)

        assert defects.public_git_text(repo, introducing_sha, fix_sha) == unaffected_text
        assert _git(repo, "show", fix_sha) != unaffected_plain_show  # the control: a plain `git show` is affected

    def test_a_non_utf8_byte_in_a_commits_diff_is_replaced_not_raised(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        (repo / "latin1.txt").write_bytes(b"caf\xe9 = 1\n")
        introducing_sha = _commit(repo, "introduce a latin-1 file")
        (repo / "latin1.txt").write_bytes(b"caf\xe9 = 2\n")
        fix_sha = _commit(repo, "fix the latin-1 file")

        text = defects.public_git_text(repo, introducing_sha, fix_sha)

        assert "caf\ufffd = 1" in text
        assert "caf\ufffd = 2" in text

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
        violation = defects.check_description_provenance(description, [], {"cand-1": self._EXCERPT})
        assert violation is not None
        assert violation.source_candidate_id == "cand-1"
        assert violation.shared_run == "error handling silently swallows the exception"

    def test_description_under_six_tokens_verbatim_quoting_a_short_excerpt_is_rejected(self):
        """A description under the six-token window still must be checked for
        verbatim-quoting a short excerpt, not waved through as "no violation"."""
        description = "silently swallows the exception here"  # 5 tokens
        excerpt = "the reviewer found that silently swallows the exception here today"
        violation = defects.check_description_provenance(description, [], {"cand-1": excerpt})
        assert violation is not None
        assert violation.source_candidate_id == "cand-1"
        assert violation.shared_run == "silently swallows the exception here"

    def test_six_word_run_also_in_public_git_text_passes(self):
        description = "The error handling silently swallows the exception in this path."
        public_text = "before the fix, error handling silently swallows the exception unconditionally"
        violation = defects.check_description_provenance(description, [public_text], {"cand-1": self._EXCERPT})
        assert violation is None

    def test_description_sharing_only_identifiers_and_code_passes(self):
        excerpt = "the `_normalize_cited_path` helper returns None for an unresolvable candidate"
        description = "`_normalize_cited_path` returned None when the candidate path could not resolve at all"
        violation = defects.check_description_provenance(description, [], {"cand-1": excerpt})
        assert violation is None

    def test_non_ascii_six_word_run_is_rejected(self):
        excerpt = "el manejo de errores descartó la excepción silenciosamente aquí"
        description = "El manejo de errores descartó la excepción silenciosamente en este caso."
        violation = defects.check_description_provenance(description, [], {"cand-1": excerpt})
        assert violation is not None

    def test_checked_against_every_candidates_excerpt_not_only_its_own(self):
        description = "The error handling silently swallows the exception in this path."
        violation = defects.check_description_provenance(
            description, [], {"unrelated-candidate": self._EXCERPT},
        )
        assert violation is not None
        assert violation.source_candidate_id == "unrelated-candidate"

    def test_a_word_run_spanning_the_join_of_two_public_texts_is_not_exempt(self):
        """The git text ends "alpha beta gamma" and the comment text starts "delta epsilon zeta", and the
        excerpt holds the six-word run across them. The texts are checked separately, so the run is not public."""
        excerpt = "the reviewer noted alpha beta gamma delta epsilon zeta again"
        description = "We saw alpha beta gamma delta epsilon zeta in review."
        violation = defects.check_description_provenance(
            description, ["fix applied. alpha beta gamma", "delta epsilon zeta is the comment"], {"cand-1": excerpt},
        )
        assert violation is not None
        assert violation.shared_run == "alpha beta gamma delta epsilon zeta"

    def test_a_run_inside_any_one_public_text_passes(self):
        description = "The error handling silently swallows the exception in this path."
        violation = defects.check_description_provenance(
            description, ["unrelated git text", "error handling silently swallows the exception, said the owner"],
            {"cand-1": self._EXCERPT},
        )
        assert violation is None

    def test_a_bare_string_in_place_of_the_list_of_texts_raises(self):
        with pytest.raises(TypeError, match="not a single string"):
            defects.check_description_provenance("x", "public text", {})


class TestDefectPublicTexts:
    _COMMENT_EVIDENCE = {"public_comment_text": "app.py:2 — This value is wrong."}

    def _git_text(self, monkeypatch) -> None:
        monkeypatch.setattr(defects, "public_git_text", lambda *args: "git text")

    def test_a_github_comment_source_adds_the_stored_comment_text_as_a_separate_text(self, monkeypatch, tmp_path):
        self._git_text(monkeypatch)
        texts = defects.defect_public_texts(
            tmp_path, _SHA_A, _SHA_B, source="pr-comment", evidence=self._COMMENT_EVIDENCE,
        )
        assert texts == ["git text", "app.py:2 — This value is wrong."]

    @pytest.mark.parametrize("source", ["review-round", "szz"])
    def test_a_source_outside_the_github_comment_sources_gets_no_exemption_from_the_evidence_field(
        self, monkeypatch, tmp_path, source,
    ):
        self._git_text(monkeypatch)
        texts = defects.defect_public_texts(
            tmp_path, _SHA_A, _SHA_B, source=source, evidence=self._COMMENT_EVIDENCE,
        )
        assert texts == ["git text"]

    @pytest.mark.parametrize(
        "evidence", [None, {}, {"public_comment_text": ""}, {"public_comment_text": 7}],
        ids=["none", "empty", "blank", "not-str"],
    )
    def test_a_github_comment_source_without_a_usable_stored_text_falls_back_to_git_text_only(
        self, monkeypatch, tmp_path, evidence,
    ):
        self._git_text(monkeypatch)
        assert defects.defect_public_texts(tmp_path, _SHA_A, _SHA_B, source="pr-comment", evidence=evidence) == ["git text"]

    def test_the_github_comment_sources_are_known_sources(self):
        assert defects.GITHUB_COMMENT_SOURCES <= defects.KNOWN_SOURCES


class TestHasDisallowedControlCharacter:
    @pytest.mark.parametrize(
        ("text", "allowed_by_default", "allowed_with_tab"),
        [
            pytest.param("plain", True, True, id="plain"),
            pytest.param("line one\nline two", True, True, id="lf"),
            pytest.param("tab\there", False, True, id="tab"),
            pytest.param("esc \x1b[31m", False, False, id="esc"),
            pytest.param("bidi \u202e override", False, False, id="bidi-override"),
            pytest.param("joiner \u200d here", False, False, id="zero-width-joiner"),
            pytest.param("carriage\rreturn", False, False, id="cr"),
        ],
    )
    def test_the_default_allows_lf_alone_and_a_caller_can_also_allow_tab(self, text, allowed_by_default, allowed_with_tab):
        assert defects.has_disallowed_control_character(text) is not allowed_by_default
        assert defects.has_disallowed_control_character(text, allowed=frozenset({"\n", "\t"})) is not allowed_with_tab


# One character of each class the description policy rejects, beside the category it belongs to. The
# first list holds the characters a mined body is counted as `control-characters` for, the second the
# ones that render as nothing and are counted as `invisible-characters`.
_REJECTED_CONTROL_CHARACTERS = [
    pytest.param("\x1b", id="cc-escape"),
    pytest.param("\x00", id="cc-nul"),
    pytest.param("\x7f", id="cc-delete"),
    pytest.param("\x85", id="cc-c1-next-line"),
    pytest.param("\x9b", id="cc-c1-eight-bit-csi"),
    pytest.param("\u2028", id="zl-line-separator"),
    pytest.param("\u2029", id="zp-paragraph-separator"),
    pytest.param("\ue000", id="co-private-use"),
    pytest.param("\ud800", id="cs-surrogate"),
    pytest.param("\ufdd0", id="cn-noncharacter-first-of-the-arabic-presentation-block"),
    pytest.param("\ufdef", id="cn-noncharacter-last-of-the-arabic-presentation-block"),
    pytest.param("\ufffe", id="cn-noncharacter-last-two-of-plane-0-first"),
    pytest.param("\uffff", id="cn-noncharacter-last-two-of-plane-0-second"),
    pytest.param("\U0001fffe", id="cn-noncharacter-last-two-of-plane-1-first"),
    pytest.param("\U0010ffff", id="cn-noncharacter-last-of-plane-16"),
]
_REJECTED_INVISIBLE_CHARACTERS = [
    pytest.param("\u202e", id="cf-bidi-override"),
    pytest.param("\u200d", id="cf-zero-width-joiner"),
    pytest.param("\u034f", id="mn-combining-grapheme-joiner"),
    pytest.param("\u17b4", id="mn-khmer-vowel-inherent-aq"),
    pytest.param("\u17b5", id="mn-khmer-vowel-inherent-aa"),
    pytest.param("\ufe0f", id="mn-variation-selector-fe0f"),
    pytest.param("\U000e0100", id="mn-supplementary-variation-selector"),
    pytest.param("\u180b", id="mn-mongolian-free-variation-selector"),
    pytest.param("\u180d", id="mn-mongolian-free-variation-selector-last-of-the-range"),
    pytest.param("\u180f", id="mn-mongolian-free-variation-selector-four"),
    pytest.param("\ufe00", id="mn-variation-selector-1"),
    pytest.param("\U000e01ef", id="mn-supplementary-variation-selector-last-of-the-range"),
    pytest.param("\u1160", id="lo-hangul-jungseong-filler"),
    pytest.param("\u3164", id="lo-hangul-filler"),
    pytest.param("\u115f", id="lo-hangul-choseong-filler"),
    pytest.param("\uffa0", id="lo-halfwidth-hangul-filler"),
    pytest.param("\u2800", id="so-braille-blank"),
    pytest.param("\u2065", id="cn-reserved-default-ignorable-in-the-general-punctuation-block"),
    pytest.param("\ufff0", id="cn-reserved-default-ignorable-in-the-specials-block"),
    pytest.param("\ufff8", id="cn-reserved-default-ignorable-last-in-the-specials-block"),
    pytest.param("\U000e0002", id="cn-reserved-default-ignorable-first-after-the-language-tag"),
    pytest.param("\U000e001f", id="cn-reserved-default-ignorable-last-before-the-tag-space"),
    pytest.param("\U000e00ff", id="cn-reserved-default-ignorable-last-before-the-variation-selectors"),
    pytest.param("\U000e0fff", id="cn-reserved-default-ignorable-last-of-the-final-range"),
    pytest.param("\U000e0000", id="cn-reserved-default-ignorable-before-the-tag-characters"),
    pytest.param("\U000e0080", id="cn-reserved-default-ignorable-after-the-tag-characters"),
    pytest.param("\U000e01f0", id="cn-reserved-default-ignorable-after-the-variation-selectors"),
]
_REJECTED_DESCRIPTION_CHARACTERS = [*_REJECTED_CONTROL_CHARACTERS, *_REJECTED_INVISIBLE_CHARACTERS]


def _with_skip_reason(character_rows, skip_reason: str):
    return [pytest.param(row.values[0], skip_reason, id=row.id) for row in character_rows]


# The code point on each side of every `_INVISIBLE_RANGES` entry that is neither in a range nor in another
# unsafe class (a neighbour that is Cf, such as U+180E, is rejected whatever the range says, so it pins no
# edge). A range widened by one code point rejects the row beside it. The unassigned rows are outside every
# unsafe class in every Unicode version the bench supports.
_VISIBLE_RANGE_NEIGHBOURS = [
    pytest.param("edge \u034e text", id="mn-before-the-combining-grapheme-joiner"),
    pytest.param("edge \u0350 text", id="mn-after-the-combining-grapheme-joiner"),
    pytest.param("edge \u17b3 text", id="lo-khmer-independent-vowel-before-the-inherent-vowels"),
    pytest.param("edge \u17b6 text", id="mc-khmer-vowel-sign-after-the-inherent-vowels"),
    pytest.param("edge \u180a text", id="po-mongolian-nirugu-before-the-variation-selectors"),
    pytest.param("edge \u1810 text", id="nd-mongolian-digit-after-the-variation-selector-four"),
    pytest.param("edge \ufdff text", id="so-arabic-ligature-before-the-variation-selectors"),
    pytest.param("edge \ufe10 text", id="po-vertical-comma-after-the-variation-selectors"),
    pytest.param("edge \u115e text", id="lo-hangul-choseong-before-the-fillers"),
    pytest.param("edge \u1161 text", id="lo-hangul-jungseong-after-the-fillers"),
    pytest.param("edge \u3163 text", id="lo-hangul-letter-before-the-hangul-filler"),
    pytest.param("edge \u3165 text", id="lo-hangul-letter-after-the-hangul-filler"),
    pytest.param("edge \uff9f text", id="lm-halfwidth-katakana-before-the-halfwidth-filler"),
    pytest.param("edge \uffa1 text", id="lo-halfwidth-hangul-letter-after-the-halfwidth-filler"),
    pytest.param("edge \uffef text", id="cn-unassigned-before-the-specials-range"),
    pytest.param("edge \U000e1000 text", id="cn-unassigned-after-the-final-range"),
    pytest.param("edge \u27ff text", id="sm-arrow-before-the-braille-blank"),
]

_PRINTABLE_DESCRIPTIONS = [
    pytest.param("plain ascii", id="ascii"),
    pytest.param("naïve café 日本語", id="accented-and-cjk"),
    pytest.param("emoji 🙂 here", id="emoji-without-a-joiner"),
    pytest.param("braille \u2801 dot", id="braille-pattern-that-draws-a-dot"),
    pytest.param("line one\nline two", id="lf"),
    pytest.param("tab\tseparated", id="tab"),
    # Unassigned in Unicode 15.0 and assigned in 15.1: accepted whichever database the interpreter holds.
    pytest.param("stroke \u31ef here", id="character-assigned-in-unicode-15-1"),
    # Unassigned in every released Unicode version: accepted, since validation cannot follow the interpreter's tables.
    pytest.param("unassigned \u0378 here", id="unassigned-character-outside-the-fixed-ranges"),
    # Spaces and right-to-left letters are visible text, so they are accepted without a bidi control.
    pytest.param("non\u00a0breaking space", id="no-break-space"),
    pytest.param("em\u2003space and ideographic\u3000space", id="other-space-separators"),
    pytest.param("\u05e9\u05dc\u05d5\u05dd hebrew", id="hebrew-word-without-a-bidi-control"),
    pytest.param("\u0645\u0631\u062d\u0628\u0627 arabic", id="arabic-word-without-a-bidi-control"),
    # The code point just outside each `_NONCHARACTER_RANGES` edge: a range widened by one code point rejects the row.
    pytest.param("edge \ufdcf text", id="so-arabic-ligature-before-the-arabic-presentation-noncharacters"),
    pytest.param("edge \ufdf0 text", id="lo-arabic-ligature-after-the-arabic-presentation-noncharacters"),
    pytest.param("edge \ufffd text", id="so-replacement-character-before-the-last-two-of-plane-0"),
    pytest.param("edge \U00010000 text", id="lo-linear-b-syllable-after-plane-0"),
    pytest.param("edge \U0001fffd text", id="cn-unassigned-before-the-last-two-of-plane-1"),
    *_VISIBLE_RANGE_NEIGHBOURS,
]


class TestDescriptionPolicy:
    """One policy decides what a stored or displayed description may hold: `is_terminal_unsafe_character`,
    enforced when a `ConfirmedDefect` is constructed, which every loader goes through, and shared by the
    mined-body check, the typed prompt, and the terminal escape."""

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_each_rejected_class_is_flagged_wherever_it_sits_in_the_text(self, character):
        assert defects.is_terminal_unsafe_character(character) is True
        for text in (character, f"before {character}", f"{character} after", f"mid{character}dle"):
            assert defects.has_disallowed_control_character(text, allowed=frozenset({"\n", "\t"})) is True

    def test_the_rejected_rows_hold_the_first_and_last_member_of_every_invisible_range(self):
        """The rows are literals, so a range edited by one code point fails its row. This guard fails
        when a range is added without rows for its edges."""
        pinned_code_points = {ord(row.values[0]) for row in _REJECTED_INVISIBLE_CHARACTERS}
        edge_code_points = {edge for low, high in defects._INVISIBLE_RANGES for edge in (low, high)}

        assert edge_code_points - pinned_code_points == set()

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_a_confirmed_defect_rejects_each_class_naming_its_code_point_and_not_the_character(self, character):
        with pytest.raises(ValueError, match=rf"U\+{ord(character):04X}") as exc_info:
            ConfirmedDefect(**_confirmed_defect_data(description=f"a {character} b"))

        assert character not in str(exc_info.value)

    @pytest.mark.parametrize("description", _PRINTABLE_DESCRIPTIONS)
    def test_a_confirmed_defect_accepts_printable_text_including_lf_and_tab(self, description):
        assert ConfirmedDefect(**_confirmed_defect_data(description=description)).description == description

    @pytest.mark.parametrize("description", [None, 7, ["a"]])
    def test_a_confirmed_defect_rejects_a_description_that_is_not_a_string(self, description):
        with pytest.raises(ValueError, match="description must be a string"):
            ConfirmedDefect(**_confirmed_defect_data(description=description))

    @pytest.mark.parametrize(
        ("field_name", "value", "code_point"),
        [
            pytest.param("id", "c1\x1b[31m", 0x1B, id="id-escape"),
            pytest.param("id", "c1\x9b", 0x9B, id="id-c1-control"),
            pytest.param("path", "app\u202e.py", 0x202E, id="path-bidi-override"),
            pytest.param("fix_date", "2024-01-01\x1b10:00:00", 0x1B, id="fix-date-escape-as-separator"),
        ],
    )
    def test_a_confirmed_defect_rejects_a_terminal_unsafe_character_in_id_path_and_fix_date(
        self, field_name, value, code_point,
    ):
        with pytest.raises(ValueError, match=rf"{field_name} holds the disallowed character U\+{code_point:04X}"):
            ConfirmedDefect(**_confirmed_defect_data(**{field_name: value}))

    @pytest.mark.parametrize(
        ("field_name", "value", "code_point"),
        [
            pytest.param("id", "c1\nx", 0x0A, id="id-line-feed"),
            pytest.param("id", "c1\tx", 0x09, id="id-tab"),
            pytest.param("path", "app.py\n### Run run-a", 0x0A, id="path-line-feed"),
            pytest.param("path", "app\t.py", 0x09, id="path-tab"),
            pytest.param("fix_date", "2024-01-01\n10:00:00", 0x0A, id="fix-date-line-feed-as-separator"),
            pytest.param("fix_date", "2024-01-01\t10:00:00", 0x09, id="fix-date-tab-as-separator"),
        ],
    )
    def test_a_confirmed_defect_rejects_lf_and_tab_in_id_path_and_fix_date_though_a_description_allows_them(
        self, field_name, value, code_point,
    ):
        with pytest.raises(ValueError, match=rf"{field_name} holds the disallowed character U\+{code_point:04X}"):
            ConfirmedDefect(**_confirmed_defect_data(**{field_name: value}))

    def test_a_confirmed_defect_accepts_printable_non_ascii_in_id_and_path(self):
        defect = ConfirmedDefect(**_confirmed_defect_data(id="c\u00e9-1", path="docs/caf\u00e9.md"))

        assert (defect.id, defect.path) == ("c\u00e9-1", "docs/caf\u00e9.md")

    @pytest.mark.parametrize("defect_id", ["", None, 7])
    def test_a_confirmed_defect_rejects_an_id_that_is_not_a_non_empty_string(self, defect_id):
        with pytest.raises(ValueError, match="id must be a non-empty string"):
            ConfirmedDefect(**_confirmed_defect_data(id=defect_id))

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_a_defects_file_holding_a_rejected_character_fails_to_load(self, tmp_path, character):
        defects_path = tmp_path / "defects.json"
        defects_path.write_text(json.dumps([_confirmed_defect_data(description=f"a {character} b")]))

        with pytest.raises(ValueError, match=rf"U\+{ord(character):04X}"):
            defects.load_confirmed_defects(defects_path)

    @pytest.mark.parametrize("description", _PRINTABLE_DESCRIPTIONS)
    def test_a_defects_file_holding_printable_text_loads(self, tmp_path, description):
        defects_path = tmp_path / "defects.json"
        defects_path.write_text(json.dumps([_confirmed_defect_data(description=description)]))

        assert [d.description for d in defects.load_confirmed_defects(defects_path)] == [description]

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_the_terminal_escape_shows_each_rejected_class_as_a_backslash_escape(self, character):
        escaped = defects.escape_for_terminal(f"a{character}b")

        assert escaped == "a" + character.encode("unicode_escape").decode("ascii") + "b"
        assert character not in escaped
        assert run_review_bench._escape_control_characters(f"a{character}b") == escaped

    @pytest.mark.parametrize("description", _PRINTABLE_DESCRIPTIONS)
    def test_the_terminal_escape_leaves_printable_text_unchanged_apart_from_line_breaks(self, description):
        assert defects.escape_for_terminal(description) == description.replace("\n", "\\n").replace("\t", "\\t")


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


class TestSzzRunGitDecoding:
    def test_a_non_utf8_byte_in_a_commits_diff_is_replaced_not_raised(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        (repo / "latin1.txt").write_bytes(b"caf\xe9 = 1\n")
        introducing_sha = _commit(repo, "introduce a latin-1 file")

        diff_text = mine_szz._run_git(["show", "--format=", introducing_sha], cwd=repo)

        assert "+caf\ufffd = 1" in diff_text


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

    @pytest.mark.parametrize("path", ["t[1].py", ":(top)x.py", ":!x.py"])
    def test_a_path_with_glob_characters_or_pathspec_magic_is_matched_literally(self, tmp_path, path):
        """Read as a pathspec, `:(top)x.py` selects `x.py` and so finds none of the named file's diff."""
        repo = _init_repo(tmp_path / "repo")
        decoy_path = "t1.py" if path == "t[1].py" else "x.py"
        _write(repo, decoy_path, "decoy_bad()\nkeep = 1\n")
        _commit(repo, "add the decoy")
        _write(repo, path, "bad_call()\nkeep = 1\n")
        introducing_sha = _commit(repo, "add the named file")
        _write(repo, path, "keep = 1\n")
        _write(repo, decoy_path, "keep = 1\n")
        fix_sha = _commit(repo, "fix both files")

        introducers, _is_low_confidence = mine_szz.blame_fix_commit(repo, fix_sha, path)

        assert introducers == frozenset({introducing_sha})


# --- mine_review_rounds: branch-ref resolution -------------------------------


class TestPathsTouchedOnBranch:
    def test_a_path_that_is_not_valid_utf8_is_listed_with_a_replacement_character_instead_of_raising(self, tmp_path):
        repo = _init_repo(tmp_path / "repo")
        _write(repo, "seed.py", "seed = 1\n")
        base_sha = _commit(repo, "seed")
        try:
            _write(repo, os.fsdecode(b"caf\xe9.py"), "named = 1\n")
        except OSError:
            pytest.skip("this filesystem rejects a non-UTF-8 file name")
        _write(repo, "plain.py", "plain = 1\n")
        _commit(repo, "touch a non-UTF-8 path and a plain one")

        touched = mine_review_rounds._paths_touched_on_branch(repo, base_sha, "HEAD")

        assert touched == frozenset({"caf\ufffd.py", "plain.py"})


class TestCommitsTouchingPath:
    @pytest.mark.parametrize("path", ["t[1].py", ":(top)x.py", ":!x.py"])
    def test_a_path_with_glob_characters_or_pathspec_magic_lists_only_the_commits_touching_that_name(
        self, tmp_path, path,
    ):
        repo = _init_repo(tmp_path / "repo")
        decoy_path = "t1.py" if path == "t[1].py" else "x.py"
        _write(repo, "seed.py", "seed = 1\n")
        base_sha = _commit(repo, "seed")
        _write(repo, path, "named = 1\n")
        named_sha = _commit(repo, "touch the named file")
        _write(repo, decoy_path, "decoy = 1\n")
        _commit(repo, "touch only the decoy")

        commits = mine_review_rounds._commits_touching_path(repo, base_sha, "HEAD", path)

        assert [sha for sha, _timestamp in commits] == [named_sha]


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

    def test_a_pr_head_whose_tree_holds_a_dot_git_entry_is_refused_and_leaves_no_local_ref(self, tmp_path):
        repo, bare = self._repo_with_bare_origin(tmp_path)

        def git_in_bare(*args: str, stdin: bytes | None = None) -> str:
            return subprocess.run(
                ["git", "-c", "user.name=Test", "-c", "user.email=test@example.com", *args],
                cwd=bare, input=stdin, capture_output=True, check=True,
            ).stdout.decode().strip()

        blob_id = git_in_bare("hash-object", "-w", "--stdin", stdin=b"hostile\n")
        # `--literally` skips the tree-format check that would reject a `.git` entry name.
        raw_tree = b"100644 .git\0" + bytes.fromhex(blob_id)
        tree_id = git_in_bare("hash-object", "-t", "tree", "--literally", "-w", "--stdin", stdin=raw_tree)
        hostile_head = git_in_bare("commit-tree", tree_id, "-m", "hostile head")
        git_in_bare("update-ref", "refs/pull/7/head", hostile_head)

        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 7)

        assert (ref_status, ref) == ("fetch-failed", None)
        unresolved = subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", "refs/review-bench/pr/7"], cwd=repo, capture_output=True,
        )
        assert unresolved.returncode != 0

    def test_missing_pr_head_records_fetch_failed(self, tmp_path):
        repo, _bare = self._repo_with_bare_origin(tmp_path)
        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 999)
        assert ref_status == "fetch-failed"
        assert ref is None

    def test_an_escape_sequence_in_a_failed_fetchs_stderr_is_not_printed_raw(self, tmp_path, monkeypatch, capsys):
        """Git's stderr can echo text the remote chose, so it reaches the terminal escaped."""
        repo, _bare = self._repo_with_bare_origin(tmp_path)
        real_run = subprocess.run

        def run_with_a_hostile_fetch_failure(command, **kwargs):
            if command[0] == "git" and "fetch" in command:
                raise subprocess.CalledProcessError(128, command, stderr="fatal: \x1b[31mred\u202e\n")
            return real_run(command, **kwargs)

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", run_with_a_hostile_fetch_failure)

        ref_status, ref = mine_review_rounds.resolve_branch_ref(repo, "nonexistent-branch", 999)

        assert (ref_status, ref) == ("fetch-failed", None)
        assert capsys.readouterr().err == (
            "mine-rounds: fetch of PR #999's head failed: fatal: \\x1b[31mred\\u202e\n"
        )

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

    def test_an_escape_sequence_in_a_failed_gh_calls_stderr_is_not_printed_raw(self, tmp_path, monkeypatch, capsys):
        """`gh` stderr can echo text the provider chose, so it reaches the terminal escaped."""
        def fake_run(*args, **kwargs):
            raise subprocess.CalledProcessError(1, args, stderr="\x1b[31mred\u202e\n")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)

        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") is None

        assert capsys.readouterr().err == (
            "mine-rounds: gh pr list for branch 'feat' failed (CalledProcessError): \\x1b[31mred\\u202e\n"
        )

    def test_the_text_of_a_non_subprocess_gh_failure_is_escaped_too(self, tmp_path, monkeypatch, capsys):
        def fake_run(*args, **kwargs):
            raise FileNotFoundError("gh \x1b[31m missing")

        monkeypatch.setattr(mine_review_rounds.subprocess, "run", fake_run)

        assert mine_review_rounds.resolve_pr_number(tmp_path, "feat") is None

        stderr = capsys.readouterr().err
        assert "gh \\x1b[31m missing" in stderr
        assert "\x1b" not in stderr


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
            ("docs/Guide.MD", "comment-discipline-reviewer", True),
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


# --- PR-comment miner ----------------------------------------------------------

_OWNER_LOGIN = "owner-login"
_REPOSITORY = "o/r"
_DISCUSSION_ANCHOR = "#" + "discussion_r"
_ORIGIN_URL = f"https://github.com/{_REPOSITORY}.git"
_MERGED_PR_NUMBER = 7
_UNMERGED_PR_NUMBER = 8
_COMMENT_ID = 1001

# `claude-skills/skills/respond-pr/SKILL.md` step 5's worked examples, copied
# with their blockquote prefix. The FIXED example prints `commit_sha: <sha>`,
# a placeholder. These reply texts and the field tokens below are hand-copied:
# no test reads SKILL.md, so an edit to its reply format does not fail them.
# Drift surfaces only as a zero-candidate run.
_RESPOND_PR_FIXED_EXAMPLE = (
    "> **[Claude Code]** Fixed. Applied the reviewer's intent (validate before writing) but checked at the handler "
    "level rather than inline at the call site — the call site is shared by three paths and an inline check would "
    "need to be duplicated. `disposition: fixed-with-modification` | `rationale: moved check to handler boundary to "
    "avoid three-way duplication` | `commit_sha: <sha>`"
)
_RESPOND_PR_EXPLAIN_DESIGN_EXAMPLE = (
    "> **[Claude Code]** The early return is intentional — when the session token is absent we want a fast 401 with "
    "no DB round-trip. The alternative (falling through to a null-check deeper in the stack) would silently succeed "
    "for unauthenticated callers in contexts where the token field is optional. `where-documented: auth/middleware.ts:42`"
)
# The step has no worked example for these, so each is written from the step's field lists.
_RESPOND_PR_OUT_OF_SCOPE_REPLY = (
    "**[Claude Code]** Real, but a separate PR. `acknowledgment: confirmed` | `where-tracked: backlog` | "
    "`link-or-ticket: will create`"
)
_RESPOND_PR_DEFERRED_REPLY = (
    "**[Claude Code]** Valid, left for later. `acknowledgment: confirmed` | `deferral-reason: needs a schema change` | "
    "`follow-up-ticket: will create: add the column`"
)
_RESPOND_PR_AGREE_NO_CHANGE_REPLY = (
    "**[Claude Code]** Agreed, and the caller already guards it. `acknowledgment: confirmed` | "
    "`rationale: the guard sits one frame up`"
)
# The wording SKILL.md's "Avoid SHA-pinned commit references" guideline prefers, beside a `disposition: fixed-` token and
# no sha.
_RESPOND_PR_LATEST_COMMIT_REPLY = (
    "**[Claude Code]** Addressed in the latest commit on this branch. `disposition: fixed-as-requested`"
)


def _fixed_reply_body(sha_text: str) -> str:
    return _RESPOND_PR_FIXED_EXAMPLE.replace("<sha>", sha_text)


class _FakeGh:
    """Stands in for the `run` that carries every `gh` call. It derives its answer from the argv: the
    visibility payload, the user lookup, and the two paginated listings, each of which must carry
    `--hostname github.com` and this repository's explicit `repos/<owner>/<repo>` path. Any other argv
    fails loudly, so no test reaches the network. Pages hold `page_size` objects and print one JSON
    array after another, joined by `page_separator`, unless `listing_stdout` replaces both listings'
    output. The call at `fail_on_call_index` raises `failure`. The call at `nonzero_exit_on_call_index`
    exits 1 with `nonzero_exit_stderr` and leaves `nonzero_exit_stdout` on stdout, as `gh api --paginate`
    leaves the pages it printed before a later page failed: it raises CalledProcessError when the caller
    passed a truthy `check`, and otherwise returns that exit status, as `subprocess.run` does. Each call's
    argv is in `calls` and its keyword arguments (`timeout`, `text`, `check`, `cwd`) are at the same index
    of `call_kwargs`."""

    def __init__(
        self, *, comments: list[dict], closed_pulls: list[dict], login: str = _OWNER_LOGIN,
        visibility_stdout: str = '{"private": false, "visibility": "public"}', page_size: int = 1,
        page_separator: str = "", fail_on_call_index: int | None = None, failure: Exception | None = None,
        nonzero_exit_on_call_index: int | None = None, nonzero_exit_stderr: str = "HTTP 502: bad gateway",
        nonzero_exit_stdout: str = "", listing_stdout: str | None = None,
    ) -> None:
        self.comments = comments
        self.closed_pulls = closed_pulls
        self.login = login
        self.visibility_stdout = visibility_stdout
        self.page_size = page_size
        self.page_separator = page_separator
        self.fail_on_call_index = fail_on_call_index
        self.failure = failure
        self.nonzero_exit_on_call_index = nonzero_exit_on_call_index
        self.nonzero_exit_stderr = nonzero_exit_stderr
        self.nonzero_exit_stdout = nonzero_exit_stdout
        self.listing_stdout = listing_stdout
        self.calls: list[list[str]] = []
        self.call_kwargs: list[dict] = []

    def _paginate(self, items: list[dict]) -> str:
        if self.listing_stdout is not None:
            return self.listing_stdout
        pages = [items[start:start + self.page_size] for start in range(0, len(items), self.page_size)] or [[]]
        return self.page_separator.join(json.dumps(page) for page in pages)

    def __call__(self, command, **kwargs):
        call_index = len(self.calls)
        self.calls.append(command)
        self.call_kwargs.append(kwargs)
        if self.fail_on_call_index == call_index:
            raise self.failure
        if self.nonzero_exit_on_call_index == call_index:
            if kwargs.get("check"):
                raise subprocess.CalledProcessError(
                    1, command, output=self.nonzero_exit_stdout, stderr=self.nonzero_exit_stderr,
                )
            return subprocess.CompletedProcess(
                command, 1, stdout=self.nonzero_exit_stdout, stderr=self.nonzero_exit_stderr,
            )
        assert command[:4] == ["gh", "api", "--hostname", "github.com"], command
        endpoint, flags = command[4], command[5:]
        if endpoint == f"repos/{_REPOSITORY}" and flags == []:
            stdout = self.visibility_stdout
        elif endpoint == "user" and flags == ["--jq", ".login"]:
            stdout = f"{self.login}\n"
        elif endpoint == f"repos/{_REPOSITORY}/pulls/comments?per_page=100" and flags == ["--paginate"]:
            stdout = self._paginate(self.comments)
        elif endpoint == f"repos/{_REPOSITORY}/pulls?state=closed&per_page=100" and flags == ["--paginate"]:
            stdout = self._paginate(self.closed_pulls)
        else:
            raise AssertionError(f"unexpected gh call: {command}")
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")


def _closed_pull(number: int, branch: str, *, merged: bool = True) -> dict:
    return {"number": number, "head": {"ref": branch}, "merged_at": "2026-01-05T00:00:00Z" if merged else None}


def _pr_comment_repo(
    tmp_path: Path, path: str = "app.py", *, bad_line: str = "bad_value",
) -> tuple[Path, str, str, str]:
    """Returns (repo, main_sha, introducing_sha, fix_sha) for local branch `feat`, which introduces
    `bad_line` as line 2 of `path` on 2026-01-02 and fixes it on 2026-01-04. `origin` names a github.com
    URL that no fetch can reach."""
    repo = _init_repo(tmp_path / "repo")
    _git(repo, "remote", "add", "origin", _ORIGIN_URL)
    _write(repo, path, "line0\n")
    main_sha = _commit_at(repo, "seed", "2026-01-01T00:00:00+0000")
    _set_origin_main(repo, main_sha)
    _git(repo, "checkout", "-q", "-b", "feat")
    _write(repo, path, f"line0\n{bad_line}\n")
    introducing_sha = _commit_at(repo, "introduce bad value", "2026-01-02T00:00:00+0000")
    _write(repo, path, "line0\ngood_value\n")
    fix_sha = _commit_at(repo, "fix wrong value", "2026-01-04T00:00:00+0000")
    return repo, main_sha, introducing_sha, fix_sha


def _pr_review_comment(original_commit_id: str, **overrides) -> dict:
    """The owner's top-level comment on `bad_value`, as the GitHub REST API shapes it. A top-level
    comment carries no `in_reply_to_id`, and this one is outdated, so its `line` is null."""
    comment = {
        "id": _COMMENT_ID, "user": {"login": _OWNER_LOGIN}, "body": "  This value is wrong.  ", "path": "app.py",
        "side": "RIGHT", "line": None, "original_line": 2, "original_commit_id": original_commit_id,
        "created_at": "2026-01-03T00:00:00Z",
        "pull_request_url": f"https://api.github.com/repos/{_REPOSITORY}/pulls/{_MERGED_PR_NUMBER}",
        "html_url": f"https://github.com/{_REPOSITORY}/pull/{_MERGED_PR_NUMBER}{_DISCUSSION_ANCHOR}{_COMMENT_ID}",
        "diff_hunk": "@@ -1 +1,2 @@\n line0\n+bad_value",
    }
    comment.update(overrides)
    return comment


def _pr_thread_reply(body: str, **overrides) -> dict:
    """A reply in the thread of the comment `_pr_review_comment` builds, by default the owner's own
    and a minute after the fix commit."""
    reply = {
        "id": 2001, "in_reply_to_id": _COMMENT_ID, "user": {"login": _OWNER_LOGIN}, "body": body,
        "created_at": "2026-01-04T00:01:00Z",
    }
    reply.update(overrides)
    return reply


def _fixed_thread_on_pull_request(introducing_sha: str, fix_sha: str, *, comment_id: int, pr_number: int) -> list[dict]:
    """The owner's comment and its FIXED reply on pull request `pr_number`, under ids of their own so
    several threads can share one listing. The reply id is `comment_id` plus 1000, the default pair's offset."""
    return [
        _pr_review_comment(
            introducing_sha, id=comment_id,
            pull_request_url=f"https://api.github.com/repos/{_REPOSITORY}/pulls/{pr_number}",
            html_url=f"https://github.com/{_REPOSITORY}/pull/{pr_number}{_DISCUSSION_ANCHOR}{comment_id}",
        ),
        _pr_thread_reply(_fixed_reply_body(fix_sha), id=comment_id + 1000, in_reply_to_id=comment_id),
    ]


def _mine_pr_comments(repo: Path, comments: list[dict], **fake_gh_kwargs):
    fake_gh_kwargs.setdefault(
        "closed_pulls", [_closed_pull(_MERGED_PR_NUMBER, "feat"), _closed_pull(_UNMERGED_PR_NUMBER, "wip", merged=False)],
    )
    fake_gh = _FakeGh(comments=comments, **fake_gh_kwargs)
    return mine_pr_comments.mine(repo, run=fake_gh), fake_gh


# The refusal is local and immediate, so this bound is a hang guard, not a measured latency.
_CANARY_GIT_TIMEOUT_S = 30


class TestNetworkGitTransportsAreRefused:
    def test_an_https_fetch_is_refused_before_any_connection(self, tmp_path):
        """A PR branch that is not local makes the miner fetch from `origin`, a github.com URL in these
        tests. `evals/conftest.py` allows only the file protocol, so that fetch ends at git's own refusal."""
        repo = _init_repo(tmp_path / "repo")
        # With the guard gone this fetch is a live request, so it must fail fast: no credential prompt, a
        # bound on the call, and an English refusal text whatever the host locale.
        canary_env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}

        result = subprocess.run(
            ["git", "fetch", "--no-tags", "https://github.com/o/r.git", "+refs/heads/*:refs/canary/*"],
            cwd=repo, capture_output=True, text=True, env=canary_env, timeout=_CANARY_GIT_TIMEOUT_S,
        )

        assert result.returncode != 0
        assert "transport 'https' not allowed" in result.stderr


class TestProviderPayloadShapesFailClosed:
    """GitHub's REST payload can hold a null or non-object `user` or `head` and a non-string `body` or
    `head.ref` (a deleted account, a null body), and the origin URL is engineer-controlled text."""

    @pytest.mark.parametrize("user", [None, "ghost", 7], ids=["null-user", "string-user", "integer-user"])
    def test_a_comment_whose_user_is_not_an_object_is_another_authors(self, user):
        comment = _pr_review_comment(_SHA_A, user=user)

        reason = mine_pr_comments._skip_reason(
            comment, login=_OWNER_LOGIN, merged_branches={_MERGED_PR_NUMBER: "feat"},
        )

        assert reason == "other-author"

    @pytest.mark.parametrize("body", [None, 7, ["text"]], ids=["null-body", "integer-body", "list-body"])
    def test_a_comment_whose_body_is_not_a_string_has_an_empty_body(self, body):
        comment = _pr_review_comment(_SHA_A, body=body)

        reason = mine_pr_comments._skip_reason(
            comment, login=_OWNER_LOGIN, merged_branches={_MERGED_PR_NUMBER: "feat"},
        )

        assert reason == "empty-body"

    def test_replies_with_a_null_user_or_a_null_body_are_not_agent_replies(self):
        marked_body = _fixed_reply_body(_SHA_A)
        replies = [
            _pr_thread_reply(marked_body, user=None),
            _pr_thread_reply(None, id=2002),
        ]

        outcome = mine_pr_comments._thread_outcome(
            _pr_review_comment(_SHA_A), replies, login=_OWNER_LOGIN, stats=Counter(),
        )

        assert outcome == "no-agent-reply"

    def test_a_merged_pull_whose_head_or_head_ref_is_not_usable_is_left_out(self, tmp_path):
        closed_pulls = [
            {"number": 11, "head": None, "merged_at": "2026-01-05T00:00:00Z"},
            {"number": 12, "head": {"ref": 7}, "merged_at": "2026-01-05T00:00:00Z"},
            _closed_pull(13, "feat"),
        ]

        branches = mine_pr_comments.fetch_merged_pr_branches(
            tmp_path, _REPOSITORY, stats=Counter(), run=_FakeGh(comments=[], closed_pulls=closed_pulls),
        )

        assert branches == {13: "feat"}

    def test_an_origin_url_that_urlsplit_rejects_names_no_repository(self):
        assert mine_pr_comments._repository_from_remote_url("https://[github.com/o/r") is None


class TestMinePrComments:
    def test_owner_comment_with_a_fixed_reply_yields_a_candidate_with_blamed_and_replied_commits(self, tmp_path):
        repo, main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert len(candidates) == 1
        candidate = candidates[0]
        assert candidate.id == f"pr-comment:{_COMMENT_ID}"
        assert candidate.source == "pr-comment"
        assert (candidate.base_commit, candidate.head_commit, candidate.fix_commit) == (
            main_sha, introducing_sha, fix_sha,
        )
        assert candidate.fix_date == "2026-01-04T00:00:00+00:00"
        assert candidate.ref_status == "local-branch"
        assert candidate.description == "app.py:2 — This value is wrong."
        assert candidate.excerpt == ""
        assert candidate.lines_exist_at_introducing_head is True
        assert candidate.reviewer_could_have_caught_it is True
        assert candidate.file_is_markdown is False
        assert candidate.evidence == {
            "path": "app.py", "comment_path": "app.py", "pr_number": _MERGED_PR_NUMBER, "comment_id": _COMMENT_ID,
            "comment_url": f"https://github.com/{_REPOSITORY}/pull/{_MERGED_PR_NUMBER}{_DISCUSSION_ANCHOR}{_COMMENT_ID}",
            "diff_hunk": "@@ -1 +1,2 @@\n line0\n+bad_value", "original_commit_id": introducing_sha,
            "created_at": "2026-01-03T00:00:00Z", "head_on_pr_branch": True,
            "public_comment_text": "app.py:2 — This value is wrong.",
        }

    def test_the_same_comment_listed_twice_fails_on_the_duplicate_candidate_id(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        with pytest.raises(ValueError, match=f"duplicate candidate id 'pr-comment:{_COMMENT_ID}'"):
            _mine_pr_comments(repo, [*thread, _pr_review_comment(introducing_sha)])

    def test_a_comment_on_a_markdown_path_is_kept_and_flagged_markdown(self, tmp_path):
        """Markdown instruction files, whose prose the engineer reviews, must not fall to a markdown filter."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path, path="docs/guide.md")
        thread = [
            _pr_review_comment(introducing_sha, path="docs/guide.md"), _pr_thread_reply(_fixed_reply_body(fix_sha)),
        ]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert len(candidates) == 1
        assert candidates[0].file_is_markdown is True
        assert candidates[0].lens == "comment-discipline-reviewer"
        assert candidates[0].description == "docs/guide.md:2 — This value is wrong."

    def test_a_path_with_a_markdown_looking_directory_is_not_markdown(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path, path="docs.md/x.py")
        thread = [
            _pr_review_comment(introducing_sha, path="docs.md/x.py"), _pr_thread_reply(_fixed_reply_body(fix_sha)),
        ]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert [candidate.file_is_markdown for candidate in candidates] == [False]
        assert candidates[0].lens != "comment-discipline-reviewer"

    def test_an_outdated_comment_with_a_null_line_is_kept_and_rendered_with_its_original_line(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha)
        assert comment["line"] is None and comment["original_line"] == 2
        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert [candidate.description for candidate in candidates] == ["app.py:2 — This value is wrong."]

    @pytest.mark.parametrize(
        ("overrides", "skip_reason"),
        [
            pytest.param({"body": "**[Claude Code]** Agreed, fixing."}, "agent-reply", id="agent-prefixed"),
            pytest.param({"body": "  \n**[Claude Code]** Agreed."}, "agent-reply", id="agent-prefixed-after-whitespace"),
            pytest.param({"body": "> **[Claude Code]** Agreed."}, "agent-reply", id="agent-prefixed-in-a-blockquote"),
            pytest.param({"in_reply_to_id": 999}, "reply", id="reply"),
            pytest.param({"user": {"login": "someone-else"}}, "other-author", id="wrong-author"),
            pytest.param(
                {"pull_request_url": f"https://api.github.com/repos/{_REPOSITORY}/pulls/{_UNMERGED_PR_NUMBER}"},
                "unmerged-pr", id="unmerged-pr",
            ),
            pytest.param({"side": "LEFT"}, "left-side", id="left-side"),
            pytest.param({"original_line": None}, "no-line", id="file-level-comment"),
            pytest.param({"body": "   "}, "empty-body", id="empty-body"),
        ],
    )
    def test_a_comment_failing_one_filter_is_skipped_and_counted(self, tmp_path, capsys, overrides, skip_reason):
        repo, _main_sha, introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha, **overrides)])

        assert candidates == []
        assert f"'{skip_reason}': 1" in capsys.readouterr().err

    def test_a_marker_in_the_middle_of_a_body_does_not_make_it_an_agent_reply(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, body="Compare with **[Claude Code]** wording.")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert len(candidates) == 1

    def test_a_null_in_reply_to_id_is_a_top_level_comment(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, in_reply_to_id=None)

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert len(candidates) == 1

    def test_the_login_match_ignores_case(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, user={"login": _OWNER_LOGIN.upper()})

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert len(candidates) == 1

    @pytest.mark.parametrize("page_separator", ["", "\n"], ids=["pages-concatenated", "pages-on-lines"])
    def test_every_page_of_a_listing_is_read_whatever_separates_the_pages(self, tmp_path, page_separator):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [
            _pr_review_comment(introducing_sha, id=1001), _pr_review_comment(introducing_sha, id=1002),
            _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001, in_reply_to_id=1001),
            _pr_thread_reply(_fixed_reply_body(fix_sha), id=2002, in_reply_to_id=1002),
        ]

        candidates, fake_gh = _mine_pr_comments(repo, thread, page_size=1, page_separator=page_separator)

        assert [candidate.id for candidate in candidates] == ["pr-comment:1001", "pr-comment:1002"]
        assert all("--jq" not in call for call in fake_gh.calls[2:])

    def test_a_closed_pr_that_was_never_merged_is_not_a_merged_pr(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(
            introducing_sha, pull_request_url=f"https://api.github.com/repos/{_REPOSITORY}/pulls/{_UNMERGED_PR_NUMBER}",
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert candidates == []
        assert "'unmerged-pr': 1" in capsys.readouterr().err

    # --- head attribution ---

    def test_the_head_is_the_blame_of_the_commented_line_not_of_the_line_the_fix_changed(self, tmp_path):
        repo, main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-B", "feat", main_sha)
        _write(repo, "app.py", "line0\nalpha_line\n")
        introducer_of_commented_line = _commit_at(repo, "add alpha", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", "line0\nalpha_line\nbeta_line\n")
        commented_commit = _commit_at(repo, "add beta", "2026-01-02T12:00:00+0000")
        _write(repo, "app.py", "line0\nalpha_line\nbeta_fixed\n")
        fix_sha = _commit_at(repo, "fix beta", "2026-01-04T00:00:00+0000")
        comment = _pr_review_comment(commented_commit, diff_hunk="@@ -1,2 +1,3 @@\n line0\n+alpha_line")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.head_commit == introducer_of_commented_line
        assert candidate.head_commit != commented_commit
        assert candidate.base_commit == main_sha
        assert candidate.fix_commit == fix_sha

    def test_blame_follows_a_block_moved_within_the_file_before_the_commented_commit(self, tmp_path):
        repo, main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-B", "feat", main_sha)
        moved_block = "def compute_total_price(items):\n    return sum(item.price for item in items)\n"
        other_block = "def render_summary_report(report):\n    return str(report.summary_text)\n"
        _write(repo, "app.py", f"line0\n{other_block}")
        _commit_at(repo, "add other block", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", f"line0\n{other_block}{moved_block}")
        introducer = _commit_at(repo, "add moved block", "2026-01-02T06:00:00+0000")
        _write(repo, "app.py", f"line0\n{moved_block}{other_block}")
        commented_commit = _commit_at(repo, "reorder blocks", "2026-01-02T12:00:00+0000")
        _write(repo, "app.py", f"line0\n{moved_block}{other_block}# tail\n")
        fix_sha = _commit_at(repo, "fix tail", "2026-01-04T00:00:00+0000")
        comment = _pr_review_comment(
            commented_commit, original_line=2,
            diff_hunk="@@ -1,2 +1,3 @@\n line0\n+def compute_total_price(items):",
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.head_commit == introducer
        # The description's line is the head's own: the block sits at line 4 there.
        assert candidate.description.startswith("app.py:4 — ")

    def test_a_head_outside_the_pr_branch_is_accepted_and_recorded_as_off_branch(self, tmp_path):
        repo, seed_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "main")
        _write(repo, "app.py", "line0\nmain_line\n")
        main_head = _commit_at(repo, "main adds a line", "2026-01-01T12:00:00+0000")
        _set_origin_main(repo, main_head)
        _git(repo, "checkout", "-q", "-B", "feat", main_head)
        _write(repo, "app.py", "line0\nmain_line\nbad_value\n")
        commented_commit = _commit_at(repo, "introduce bad value", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", "line0\nmain_line\ngood_value\n")
        fix_sha = _commit_at(repo, "fix wrong value", "2026-01-04T00:00:00+0000")
        # The comment sits on a context line that blames to the older main commit.
        comment = _pr_review_comment(commented_commit, diff_hunk="@@ -1,2 +1,3 @@\n line0\n main_line")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.head_commit == main_head
        assert candidate.base_commit == seed_sha
        assert candidate.evidence["head_on_pr_branch"] is False

    def test_a_cross_file_blame_uses_the_head_path_for_the_kind_the_lens_and_the_description(self, tmp_path):
        repo, main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-B", "feat", main_sha)
        _write(repo, "old_name.py", "line0\nbad_value\nline2\n")
        introducer = _commit_at(repo, "add old_name", "2026-01-02T00:00:00+0000")
        (repo / "docs").mkdir()
        _git(repo, "mv", "old_name.py", "docs/notes.md")
        commented_commit = _commit_at(repo, "rename to markdown", "2026-01-02T12:00:00+0000")
        _write(repo, "docs/notes.md", "line0\ngood_value\nline2\n")
        fix_sha = _commit_at(repo, "fix value", "2026-01-04T00:00:00+0000")
        comment = _pr_review_comment(commented_commit, path="docs/notes.md")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.head_commit == introducer
        assert candidate.evidence["path"] == "old_name.py"
        assert candidate.evidence["comment_path"] == "docs/notes.md"
        assert candidate.file_is_markdown is False
        assert candidate.lens == defects.guess_lens("old_name.py")
        assert candidate.lens != "comment-discipline-reviewer"
        assert candidate.description == "old_name.py:2 — This value is wrong."

    def test_a_blame_through_a_rename_from_a_tab_named_file_leaves_the_head_unresolved(self, tmp_path, capsys):
        """Git quotes a source filename holding a TAB in its blame output, and a quoted name is no path."""
        repo, main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-B", "feat", main_sha)
        _write(repo, "old\tname.py", "line0\nbad_value\nline2\n")
        _commit_at(repo, "add a tab-named file", "2026-01-02T00:00:00+0000")
        (repo / "docs").mkdir()
        _git(repo, "mv", "old\tname.py", "docs/notes.md")
        commented_commit = _commit_at(repo, "rename to markdown", "2026-01-02T12:00:00+0000")
        _write(repo, "docs/notes.md", "line0\ngood_value\nline2\n")
        fix_sha = _commit_at(repo, "fix value", "2026-01-04T00:00:00+0000")
        comment = _pr_review_comment(commented_commit, path="docs/notes.md")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert candidates == []
        assert "'head-unresolved': 1" in capsys.readouterr().err

    def test_the_fetched_ref_status_of_a_pr_head_ref_is_recorded_on_the_candidate(self, tmp_path, monkeypatch):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setattr(mine_review_rounds, "resolve_branch_ref", lambda *args: ("fetched", "refs/heads/feat"))

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert [candidate.ref_status for candidate in candidates] == ["fetched"]

    def test_a_pr_branch_that_cannot_be_fetched_exits_2_naming_the_count_and_writes_nothing(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        with pytest.raises(SystemExit) as exit_info:
            _mine_pr_comments(repo, thread, closed_pulls=[_closed_pull(_MERGED_PR_NUMBER, "deleted-branch")])

        assert exit_info.value.code == 2
        stderr = capsys.readouterr().err
        assert "'fetch-failed': 1" in stderr
        assert (
            f"1 comment(s) skipped: PR head(s) #{_MERGED_PR_NUMBER} could not be fetched "
            "(git's error for each is above) -- nothing written; a rerun clears a network, auth, "
            "or ref-lock failure, not a pull ref the remote lacks or a fetch that exceeds the "
            f"{mine_review_rounds._GIT_FETCH_TIMEOUT_S:g}s fetch timeout, which scales with the head's size"
        ) in stderr

    def test_several_unfetchable_pr_heads_are_named_in_numeric_order_beside_the_count_of_their_comments(
        self, tmp_path, capsys,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        higher_pr_number = _MERGED_PR_NUMBER + 5
        assert str(higher_pr_number) < str(_MERGED_PR_NUMBER)  # a text sort would list the higher number first
        thread = [
            *_fixed_thread_on_pull_request(introducing_sha, fix_sha, comment_id=1001, pr_number=higher_pr_number),
            *_fixed_thread_on_pull_request(introducing_sha, fix_sha, comment_id=1002, pr_number=_MERGED_PR_NUMBER),
            *_fixed_thread_on_pull_request(introducing_sha, fix_sha, comment_id=1003, pr_number=_MERGED_PR_NUMBER),
        ]
        closed_pulls = [
            _closed_pull(_MERGED_PR_NUMBER, "deleted-branch"), _closed_pull(higher_pr_number, "other-deleted-branch"),
        ]

        with pytest.raises(SystemExit) as exit_info:
            _mine_pr_comments(repo, thread, closed_pulls=closed_pulls)

        assert exit_info.value.code == 2
        assert (
            f"3 comment(s) skipped: PR head(s) #{_MERGED_PR_NUMBER}, #{higher_pr_number} could not be fetched "
        ) in capsys.readouterr().err

    def test_a_pr_with_no_fixed_reply_is_never_fetched(self, tmp_path, monkeypatch):
        repo, _main_sha, introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setattr(
            mine_review_rounds, "resolve_branch_ref", lambda *args: pytest.fail("resolved a branch with nothing to mine"),
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha)])

        assert candidates == []

    def test_a_comment_made_on_a_commit_the_branch_does_not_reach_is_skipped(self, tmp_path, capsys):
        repo, main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "force-pushed-away", main_sha)
        _write(repo, "app.py", "line0\nbad_value\n")
        orphaned_sha = _commit_at(repo, "rewritten history", "2026-01-02T12:00:00+0000")
        assert orphaned_sha != introducing_sha
        _git(repo, "checkout", "-q", "feat")

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(orphaned_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert candidates == []
        assert "'original-commit-unreachable': 1" in capsys.readouterr().err

    # --- the FIXED reply ---

    @pytest.mark.parametrize(
        ("reply_body", "skip_reason"),
        [
            pytest.param(_RESPOND_PR_EXPLAIN_DESIGN_EXAMPLE, "not-fixed", id="explain-design"),
            pytest.param(_RESPOND_PR_AGREE_NO_CHANGE_REPLY, "not-fixed", id="agree-no-change"),
            pytest.param(_RESPOND_PR_FIXED_EXAMPLE, "fixed-without-sha", id="fixed-example-with-the-placeholder-sha"),
            pytest.param(_fixed_reply_body(""), "fixed-without-sha", id="fixed-example-with-the-sha-stripped"),
            pytest.param(_RESPOND_PR_LATEST_COMMIT_REPLY, "fixed-without-sha", id="addressed-in-the-latest-commit"),
            pytest.param(_fixed_reply_body("abc123"), "fixed-without-sha", id="six-hex-sha"),
            pytest.param(_fixed_reply_body("not-a-sha"), "fixed-without-sha", id="garbage-sha"),
            pytest.param(_RESPOND_PR_OUT_OF_SCOPE_REPLY, "out-of-scope-or-deferred", id="out-of-scope"),
            pytest.param(_RESPOND_PR_DEFERRED_REPLY, "out-of-scope-or-deferred", id="deferred"),
        ],
    )
    def test_a_thread_without_a_usable_fixed_reply_is_skipped_under_its_own_reason(
        self, tmp_path, capsys, reply_body, skip_reason,
    ):
        """The explain-design case guards the rule that a later commit touching the path is not enough: only a
        FIXED reply's own sha names the fix."""
        repo, _main_sha, introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)  # a later commit touches app.py

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(reply_body)])

        assert candidates == []
        stderr = capsys.readouterr().err
        assert f"'{skip_reason}': 1" in stderr
        assert all(f"'{other}'" not in stderr for other in {
            "not-fixed", "fixed-without-sha", "out-of-scope-or-deferred",
        } - {skip_reason})

    @pytest.mark.parametrize(
        "reply_overrides",
        [
            pytest.param(None, id="no-reply"),
            pytest.param({"user": {"login": "someone-else"}}, id="reply-by-another-login"),
            pytest.param({"body": "Fixed in the last commit. `disposition: fixed-as-requested`"}, id="reply-without-the-marker"),
            pytest.param({"in_reply_to_id": 5555}, id="reply-to-a-different-comment"),
        ],
    )
    def test_a_thread_with_no_eligible_reply_is_counted_as_no_agent_reply(self, tmp_path, capsys, reply_overrides):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha)]
        if reply_overrides is not None:
            thread.append({**_pr_thread_reply(_fixed_reply_body(fix_sha)), **reply_overrides})

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'no-agent-reply': 1" in capsys.readouterr().err

    def test_a_short_sha_in_the_reply_is_stored_as_the_full_sha(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha[:7]))],
        )

        assert [candidate.fix_commit for candidate in candidates] == [fix_sha]

    def test_the_latest_fixed_reply_by_created_at_wins_whatever_the_listing_order(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        _write(repo, "app.py", "line0\nbetter_value\n")
        second_fix_sha = _commit_at(repo, "fix again", "2026-01-04T12:00:00+0000")
        earlier_reply = _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001, created_at="2026-01-04T00:01:00Z")
        later_reply = _pr_thread_reply(_fixed_reply_body(second_fix_sha), id=2002, created_at="2026-01-04T12:01:00Z")

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), later_reply, earlier_reply])

        assert [candidate.fix_commit for candidate in candidates] == [second_fix_sha]

    def test_a_later_reply_that_is_not_fixed_does_not_displace_an_earlier_fixed_reply(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        fixed_reply = _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001, created_at="2026-01-04T00:01:00Z")
        later_explanation = _pr_thread_reply(
            _RESPOND_PR_EXPLAIN_DESIGN_EXAMPLE, id=2002, created_at="2026-01-05T00:00:00Z",
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), fixed_reply, later_explanation])

        assert [candidate.fix_commit for candidate in candidates] == [fix_sha]

    def test_a_later_fixed_reply_whose_sha_does_not_resolve_does_not_fall_back_to_an_earlier_one(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        good_reply = _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001, created_at="2026-01-04T00:01:00Z")
        unresolvable_reply = _pr_thread_reply(_fixed_reply_body("deadbee"), id=2002, created_at="2026-01-04T12:00:00Z")

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), good_reply, unresolvable_reply])

        assert candidates == []
        assert "'fix-sha-unresolvable': 1" in capsys.readouterr().err

    def test_a_fix_sha_that_names_a_commit_off_the_pr_branch_is_unresolvable(self, tmp_path, capsys):
        repo, main_sha, introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-b", "elsewhere", main_sha)
        _write(repo, "other.py", "x = 1\n")
        off_branch_sha = _commit_at(repo, "unrelated", "2026-01-04T00:00:00+0000")
        _git(repo, "checkout", "-q", "feat")

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(off_branch_sha))],
        )

        assert candidates == []
        assert "'fix-sha-unresolvable': 1" in capsys.readouterr().err

    @pytest.mark.parametrize("fix_names", ["the-seed-commit", "the-head-itself"])
    def test_a_head_that_is_not_a_strict_ancestor_of_the_fix_is_skipped(self, tmp_path, capsys, fix_names):
        repo, main_sha, introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        cited_sha = main_sha if fix_names == "the-seed-commit" else introducing_sha

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(cited_sha))],
        )

        assert candidates == []
        assert "'fix-not-descendant': 1" in capsys.readouterr().err

    # --- anchor checks ---

    @pytest.mark.parametrize(
        "diff_hunk",
        [
            pytest.param("@@ -1 +1,2 @@\n line0\n+some_other_line", id="text-differs"),
            pytest.param("@@ -1 +1,2 @@\n line0\n+", id="blank-anchor"),
            pytest.param("", id="no-hunk"),
            pytest.param("@@ -1,2 +1 @@\n line0\n-bad_value", id="removed-line"),
        ],
    )
    def test_a_commented_line_the_original_commit_does_not_hold_is_an_anchor_mismatch(
        self, tmp_path, capsys, diff_hunk,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, diff_hunk=diff_hunk)

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert candidates == []
        assert "'anchor-mismatch': 1" in capsys.readouterr().err

    def test_a_commented_line_of_non_ascii_prose_anchors_across_whitespace_and_quote_characters(self, tmp_path):
        """Markdown prose carries em-dashes, curly quotes, and no-break spaces, which the hunk and the file may spell
        with different whitespace characters."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(
            tmp_path, bad_line="\u201cUse it\u201d \u2014 never\u00a0that, \u2018ever\u2019.",
        )
        comment = _pr_review_comment(
            introducing_sha, diff_hunk="@@ -1 +1,2 @@\n line0\n+\u201cUse it\u201d \u2014 never that, \u2018ever\u2019.",
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert [candidate.id for candidate in candidates] == [f"pr-comment:{_COMMENT_ID}"]

    def test_an_original_line_past_the_end_of_the_file_is_an_anchor_mismatch(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, original_line=99)

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert candidates == []
        assert "'anchor-mismatch': 1" in capsys.readouterr().err

    def test_a_multi_line_range_comment_anchors_on_its_end_line(self, tmp_path):
        """A range comment's `original_line` is the range's last line and its hunk ends there, so the
        anchor is the end line and `original_start_line` plays no part."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path, bad_line="first_bad\nsecond_bad")
        comment = _pr_review_comment(
            introducing_sha, original_start_line=2, original_line=3,
            diff_hunk="@@ -1 +1,3 @@\n line0\n+first_bad\n+second_bad",
        )

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.description == "app.py:3 — This value is wrong."

    def test_whitespace_differences_between_the_hunk_and_the_file_do_not_break_the_anchor(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, diff_hunk="@@ -1 +1,2 @@\n line0\n+    bad_ value  ")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert len(candidates) == 1

    @pytest.mark.parametrize(
        ("blamed_path", "blamed_line"),
        [
            pytest.param("app.py", 1, id="a-different-line-at-the-head"),
            pytest.param("gone.py", 2, id="a-missing-file-at-the-head"),
        ],
    )
    def test_a_head_that_does_not_hold_the_commented_line_is_an_anchor_mismatch(
        self, tmp_path, monkeypatch, capsys, blamed_path, blamed_line,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setattr(
            mine_pr_comments, "blame_commented_line",
            lambda *args: mine_pr_comments.BlamedLine(introducing_sha, blamed_path, blamed_line),
        )

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert candidates == []
        assert "'anchor-mismatch': 1" in capsys.readouterr().err

    def test_a_blame_that_fails_leaves_the_head_unresolved(self, tmp_path, monkeypatch, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setattr(mine_pr_comments, "blame_commented_line", lambda *args: None)

        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert candidates == []
        assert "'head-unresolved': 1" in capsys.readouterr().err

    # --- the mined text ---

    @pytest.mark.parametrize(
        ("body", "expected_description_body", "skip_reason"),
        [
            pytest.param("Tab\tkept", "Tab\tkept", None, id="tab-kept"),
            pytest.param("line one\nline two", "line one\nline two", None, id="lf-kept"),
            pytest.param("line one\r\nline two", "line one\nline two", None, id="crlf-normalized"),
            pytest.param("line one\rline two", "line one\nline two", None, id="lone-cr-normalized"),
            pytest.param("naïve café 日本語 🙂", "naïve café 日本語 🙂", None, id="printable-non-ascii-kept"),
            pytest.param(
                "non\u00a0breaking \u05e9\u05dc\u05d5\u05dd", "non\u00a0breaking \u05e9\u05dc\u05d5\u05dd", None,
                id="no-break-space-and-hebrew-kept",
            ),
            pytest.param("vertical \x0b tab", None, "control-characters", id="vertical-tab-skipped"),
        ],
    )
    def test_a_mined_body_keeps_lf_and_tab_normalizes_cr_and_skips_any_other_control_character(
        self, tmp_path, capsys, body, expected_description_body, skip_reason,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha, body=body), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        if expected_description_body is None:
            assert candidates == []
            assert f"'{skip_reason}': 1" in capsys.readouterr().err
        else:
            assert [candidate.description for candidate in candidates] == [f"app.py:2 — {expected_description_body}"]
            assert candidates[0].evidence["public_comment_text"] == candidates[0].description

    @pytest.mark.parametrize(
        ("character", "skip_reason"),
        [
            *_with_skip_reason(_REJECTED_CONTROL_CHARACTERS, "control-characters"),
            *_with_skip_reason(_REJECTED_INVISIBLE_CHARACTERS, "invisible-characters"),
        ],
    )
    def test_a_mined_body_holding_any_class_the_description_policy_rejects_is_skipped_under_its_own_reason(
        self, tmp_path, capsys, character, skip_reason,
    ):
        """The same policy `ConfirmedDefect` enforces, so a body is skipped at mine time and never reaches
        the prompt, where the character would show as nothing. A character that renders as nothing is
        counted apart from a control character, so a comment dropped for a joiner or variation selector
        does not read as hostile input."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [
            _pr_review_comment(introducing_sha, body=f"looks {character} fine"),
            _pr_thread_reply(_fixed_reply_body(fix_sha)),
        ]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        stderr = capsys.readouterr().err
        assert f"'{skip_reason}': 1" in stderr
        other_reason = "invisible-characters" if skip_reason == "control-characters" else "control-characters"
        assert other_reason not in stderr

    @pytest.mark.parametrize(
        ("body", "skip_reason"),
        [
            pytest.param("joiner \u200d then escape \x1b[31m", "invisible-characters", id="invisible-first"),
            pytest.param("escape \x1b[31m then joiner \u200d", "control-characters", id="control-first"),
        ],
    )
    def test_a_mined_body_holding_both_classes_is_counted_under_its_first_disallowed_character(
        self, tmp_path, capsys, body, skip_reason,
    ):
        """The body is skipped whichever class comes first. The count label follows the first character
        `first_disallowed_character` returns, so a joiner ahead of an escape sequence reads as the benign class."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha, body=body), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        stderr = capsys.readouterr().err
        assert f"'{skip_reason}': 1" in stderr
        other_reason = "invisible-characters" if skip_reason == "control-characters" else "control-characters"
        assert other_reason not in stderr

    # --- the provider and the repository ---

    @pytest.mark.parametrize(
        ("origin_url", "expected_repository"),
        [
            pytest.param("https://github.com/o/r", "o/r", id="https"),
            pytest.param("https://github.com/o/r.git", "o/r", id="https-dot-git"),
            pytest.param("https://token@github.com/o/r.git", "o/r", id="https-with-userinfo"),
            pytest.param("git@github.com:o/r.git", "o/r", id="scp-like"),
            pytest.param("ssh://git@github.com/o/r.git", "o/r", id="ssh-url"),
            pytest.param("https://github.example.com/o/r.git", None, id="non-default-host"),
            pytest.param("git@gh-alias:o/r.git", None, id="host-alias"),
            pytest.param("https://github.com.example/o/r.git", None, id="suffix-lookalike"),
            pytest.param("https://github.com@other-host/o/r.git", None, id="userinfo-lookalike"),
            pytest.param("github.com@other-host:o/r.git", None, id="scp-like-userinfo-lookalike"),
            pytest.param("https://GitHub.com/o/r.git", None, id="uppercase-host"),
            pytest.param("https://github.com./o/r.git", None, id="trailing-dot-host"),
            pytest.param("ssh://git@ssh.github.com/o/r.git", None, id="ssh-github-com"),
            pytest.param("https://github.com/o", None, id="no-repository-segment"),
            pytest.param("https://github.com/o/r/extra", None, id="extra-path-segment"),
            pytest.param("https://github.com/o/..", None, id="dot-segment"),
            pytest.param("/srv/git/r.git", None, id="local-path"),
            pytest.param("ssh://github.com/o/r.git", "o/r", id="ssh-url-without-userinfo"),
            pytest.param("https://github.com:443/o/r", None, id="https-with-a-port"),
            pytest.param("ssh://git@github.com:22/o/r", None, id="ssh-with-a-port"),
            pytest.param("http://github.com/o/r", None, id="http-scheme"),
            pytest.param("git://github.com/o/r.git", None, id="git-scheme"),
            pytest.param("file://github.com/o/r.git", None, id="file-scheme"),
            pytest.param("https://github.com/o%2Fx/r", None, id="percent-encoded-segment"),
            pytest.param("https://github.com/o/r%20x", None, id="percent-encoded-space"),
            pytest.param("https://github.com/o /r", None, id="space-in-a-segment"),
            pytest.param("https://github.com/o/r?x=1", None, id="query-string"),
            pytest.param("https://github.com/o/r" + "#" + "fragment", None, id="fragment"),
            pytest.param("git@github.com:o/r?x=1", None, id="scp-like-with-a-question-mark"),
        ],
    )
    def test_the_repository_is_resolved_from_origin_and_only_for_exactly_github_com(
        self, tmp_path, capsys, origin_url, expected_repository,
    ):
        repo = _init_repo(tmp_path / "repo")
        _git(repo, "remote", "add", "origin", origin_url)
        fake_gh = _FakeGh(comments=[], closed_pulls=[])

        if expected_repository is not None:
            assert mine_pr_comments.resolve_origin_repository(repo) == expected_repository
            return
        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)
        assert exit_info.value.code == 2
        assert fake_gh.calls == []  # no gh call, so no endpoint was read
        assert capsys.readouterr().err == (
            "mine-pr-comments: origin remote is missing or does not name a repository on github.com, "
            "or git could not read this checkout\n"
        )

    def test_a_repository_with_no_origin_remote_exits_2(self, tmp_path, capsys):
        repo = _init_repo(tmp_path / "repo")
        fake_gh = _FakeGh(comments=[], closed_pulls=[])

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        assert fake_gh.calls == []
        assert "origin remote is missing" in capsys.readouterr().err

    def test_the_harness_checkouts_origin_is_resolved_whatever_the_working_directory(self, tmp_path, monkeypatch):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        other_repo = _init_repo(tmp_path / "other-repo")
        _git(other_repo, "remote", "add", "origin", "https://github.com/someone/else.git")
        monkeypatch.chdir(other_repo)

        candidates, fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert len(candidates) == 1
        assert all(call[4] == "user" or call[4].startswith(f"repos/{_REPOSITORY}") for call in fake_gh.calls)

    def test_every_gh_call_names_the_explicit_repository_path_and_host_under_a_conflicting_environment(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setenv("GH_REPO", "other-owner/other-repo")
        monkeypatch.setenv("GH_HOST", "ghe.example.com")

        candidates, fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert len(candidates) == 1
        assert [call[:5] for call in fake_gh.calls] == [
            ["gh", "api", "--hostname", "github.com", endpoint]
            for endpoint in (
                f"repos/{_REPOSITORY}", "user", f"repos/{_REPOSITORY}/pulls/comments?per_page=100",
                f"repos/{_REPOSITORY}/pulls?state=closed&per_page=100",
            )
        ]
        assert not any("{owner}" in argument or "{repo}" in argument for call in fake_gh.calls for argument in call)
        assert f"mine-pr-comments: reading {_REPOSITORY} as {_OWNER_LOGIN}" in capsys.readouterr().err

    def test_every_gh_call_is_bounded_by_its_own_timeout_and_runs_in_the_checkout_with_text_output(self, tmp_path):
        """A paginated listing needs more time than one call, so wiring a listing to the single-call bound
        times out on a large repository, and a dropped `timeout` is an unbounded hang."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)

        _candidates, fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )

        assert [(call[4], kwargs["timeout"]) for call, kwargs in zip(fake_gh.calls, fake_gh.call_kwargs, strict=True)] == [
            (f"repos/{_REPOSITORY}", mine_review_rounds._GH_CALL_TIMEOUT_S),
            ("user", mine_review_rounds._GH_CALL_TIMEOUT_S),
            (f"repos/{_REPOSITORY}/pulls/comments?per_page=100", mine_pr_comments._GH_COMMENTS_LISTING_TIMEOUT_S),
            (f"repos/{_REPOSITORY}/pulls?state=closed&per_page=100", mine_pr_comments._GH_CLOSED_PULLS_LISTING_TIMEOUT_S),
        ]
        for kwargs in fake_gh.call_kwargs:
            assert kwargs["text"] is True
            assert kwargs["check"] is True
            assert kwargs["capture_output"] is True
            assert kwargs["cwd"] == repo

    @pytest.mark.parametrize(
        "visibility_stdout",
        [
            pytest.param('{"private": true, "visibility": "private"}', id="private"),
            pytest.param('{"private": false, "visibility": "internal"}', id="private-false-beside-internal"),
            pytest.param('{"private": true, "visibility": "internal"}', id="internal"),
            pytest.param('{"private": false, "visibility": "private"}', id="private-false-beside-private"),
            pytest.param('{"private": false, "visibility": null}', id="null-visibility"),
            pytest.param("", id="empty"),
            pytest.param("not json", id="not-json"),
            pytest.param("[]", id="not-an-object"),
            pytest.param('{"visibility": "public"}', id="private-absent"),
            pytest.param('{"private": null}', id="private-null"),
            pytest.param('{"private": "false"}', id="private-string"),
            pytest.param('{"private": 0}', id="private-integer"),
        ],
    )
    def test_a_repository_the_provider_does_not_explicitly_report_public_is_refused_first(
        self, tmp_path, capsys, visibility_stdout,
    ):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(comments=[], closed_pulls=[], visibility_stdout=visibility_stdout)

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        stderr = capsys.readouterr().err
        assert "refusing to run" in stderr
        assert _REPOSITORY not in stderr
        assert len(fake_gh.calls) == 1  # the visibility call is the only gh call

    @pytest.mark.parametrize("visibility_stdout", ['{"private": false}', '{"private": false, "visibility": "public"}'])
    def test_a_repository_reported_public_proceeds(self, tmp_path, visibility_stdout):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(comments=[], closed_pulls=[], visibility_stdout=visibility_stdout)

        assert mine_pr_comments.require_public_repo(repo, _REPOSITORY, run=fake_gh) is None

    @pytest.mark.parametrize(
        "failure",
        [
            subprocess.CalledProcessError(1, ["gh"], stderr="HTTP 404: https://api.github.com/repos/o/r"),
            subprocess.TimeoutExpired(["gh"], 30),
            FileNotFoundError("gh not found on PATH"),
        ],
        ids=["gh-fails", "gh-times-out", "gh-missing"],
    )
    @pytest.mark.parametrize(
        ("failing_call_index", "failed_call_label"),
        [(0, "repository visibility"), (1, "user"), (2, "pulls/comments listing"), (3, "closed pulls listing")],
    )
    def test_a_failed_gh_call_exits_2_without_a_shortlist_and_the_visibility_failure_names_no_repository(
        self, tmp_path, capsys, failure, failing_call_index, failed_call_label,
    ):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(comments=[], closed_pulls=[], fail_on_call_index=failing_call_index, failure=failure)

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        stderr = capsys.readouterr().err
        assert f"mine-pr-comments: gh api {failed_call_label} failed" in stderr
        if failing_call_index == 0:
            assert _REPOSITORY not in stderr

    @pytest.mark.parametrize(
        ("failing_call_index", "failed_call_label"),
        [(0, "repository visibility"), (1, "user"), (2, "pulls/comments listing"), (3, "closed pulls listing")],
    )
    def test_a_gh_call_that_exits_non_zero_exits_2_whatever_stdout_it_left(
        self, tmp_path, capsys, failing_call_index, failed_call_label,
    ):
        """The fake honors `check=` as `subprocess.run` does, so a caller that drops `check=True` reads an
        empty stdout from a failed listing as an empty listing and mines nothing."""
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(comments=[], closed_pulls=[], nonzero_exit_on_call_index=failing_call_index)

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        stderr = capsys.readouterr().err
        assert f"mine-pr-comments: gh api {failed_call_label} failed (CalledProcessError)" in stderr
        if failing_call_index == 0:
            assert "502" not in stderr  # the visibility call shows no detail

    @pytest.mark.parametrize(
        ("failing_call_index", "failed_call_label", "first_page"),
        [
            pytest.param(2, "pulls/comments listing", [_pr_review_comment("a" * 40)], id="comments-listing"),
            pytest.param(3, "closed pulls listing", [_closed_pull(_MERGED_PR_NUMBER, "feat")], id="closed-pulls-listing"),
        ],
    )
    def test_a_listing_that_fails_after_printing_a_valid_first_page_exits_2_rather_than_mining_the_page(
        self, tmp_path, capsys, failing_call_index, failed_call_label, first_page,
    ):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(
            comments=[], closed_pulls=[], nonzero_exit_on_call_index=failing_call_index,
            nonzero_exit_stdout=json.dumps(first_page),
        )

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        assert f"mine-pr-comments: gh api {failed_call_label} failed (CalledProcessError)" in capsys.readouterr().err

    def test_the_detail_of_a_failed_gh_call_is_escaped_before_it_reaches_the_terminal(self, tmp_path, capsys):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(
            comments=[], closed_pulls=[], nonzero_exit_on_call_index=1, nonzero_exit_stderr="\x1b[31mboom\r\u202e",
        )

        with pytest.raises(SystemExit):
            mine_pr_comments.mine(repo, run=fake_gh)

        stderr = capsys.readouterr().err
        assert "\\x1b[31mboom\\r\\u202e" in stderr
        assert all(raw not in stderr for raw in ("\x1b", "\r", "\u202e"))

    @pytest.mark.parametrize(
        "login",
        [
            pytest.param("", id="empty"),
            pytest.param("owner login", id="space"),
            pytest.param("owner\nlogin", id="newline-inside"),
            pytest.param("first-line\nsecond-line", id="two-lines"),
            pytest.param("owner\tlogin", id="tab"),
            pytest.param("owner.login", id="dot"),
            pytest.param("owner/login", id="slash"),
        ],
    )
    def test_a_login_that_is_not_one_plain_handle_exits_2(self, tmp_path, login):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(login=login, comments=[], closed_pulls=[])

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2
        assert len(fake_gh.calls) == 2  # no listing is read under an unusable login

    @pytest.mark.parametrize("login", ["owner-login", "Owner_Login-1"])
    def test_a_plain_handle_login_proceeds_to_the_listings(self, tmp_path, login):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(login=login, comments=[], closed_pulls=[])

        assert mine_pr_comments.mine(repo, run=fake_gh) == []
        assert len(fake_gh.calls) == 4

    @pytest.mark.parametrize("listing_stdout", ["not json", '{"not": "an array"}'], ids=["not-json", "not-an-array"])
    def test_a_listing_page_that_is_not_a_json_array_exits_2(self, tmp_path, listing_stdout):
        repo, _main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        fake_gh = _FakeGh(comments=[], closed_pulls=[], listing_stdout=listing_stdout)

        with pytest.raises(SystemExit) as exit_info:
            mine_pr_comments.mine(repo, run=fake_gh)

        assert exit_info.value.code == 2

    def test_a_page_item_that_is_not_an_object_is_dropped_and_counted_per_listing(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment(introducing_sha), "junk", 5, _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(
            repo, thread, closed_pulls=[_closed_pull(_MERGED_PR_NUMBER, "feat"), None],
        )

        assert len(candidates) == 1
        stderr = capsys.readouterr().err
        assert "'non-object-comment-item': 2" in stderr
        assert "'non-object-pull-item': 1" in stderr

    def test_the_written_shortlist_passes_confirm_without_rejection(self, tmp_path, monkeypatch, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        candidates, _fake_gh = _mine_pr_comments(
            repo, [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha))],
        )
        local_dir = tmp_path / "local"
        defects.save_candidates(local_dir / "pr_comment_candidates.json", candidates)
        _feed_answers(monkeypatch, terminal=False)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        assert "1 candidate(s) awaiting the engineer at a terminal, 0 rejected" in capsys.readouterr().err

    # --- a failure of the run is not a verdict on the comment ---

    @staticmethod
    def _fail_git_calls(monkeypatch, command_prefix: tuple[str, ...], outcome) -> list[list[str]]:
        """Makes each `git` call whose arguments start with `command_prefix` fail: `outcome` is an exception
        to raise or an exit status to return. Returns every git command line run, so a test can read what
        reached git."""
        real_run = subprocess.run
        git_commands: list[list[str]] = []

        def run(command, **kwargs):
            if command[0] == "git":
                git_commands.append(list(command))
                if tuple(command[1:1 + len(command_prefix)]) == command_prefix:
                    if isinstance(outcome, Exception):
                        raise outcome
                    return subprocess.CompletedProcess(command, outcome, stdout=b"", stderr=b"")
            return real_run(command, **kwargs)

        monkeypatch.setattr(subprocess, "run", run)
        return git_commands

    def _fixed_thread(self, tmp_path, **comment_overrides):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        return repo, [_pr_review_comment(introducing_sha, **comment_overrides), _pr_thread_reply(_fixed_reply_body(fix_sha))]

    @pytest.mark.parametrize(
        ("git_prefix", "subcommand"),
        [
            pytest.param(("merge-base", "--is-ancestor"), "merge-base", id="is-ancestor"),
            pytest.param(("show",), "show", id="show-the-file"),
            pytest.param(("-c", "core.quotePath=false", "blame"), "blame", id="blame"),
            pytest.param(("rev-parse", "--verify", "--quiet"), "rev-parse", id="rev-parse"),
            pytest.param(("log", "-1", "--format=%aI"), "log", id="fix-date"),
        ],
    )
    @pytest.mark.parametrize(
        "outcome",
        [subprocess.TimeoutExpired(["git"], 10), FileNotFoundError("git"), 129, -9],
        ids=["timeout", "git-missing", "usage-error-status", "killed-by-a-signal"],
    )
    def test_a_git_call_that_gives_no_answer_skips_that_comment_as_a_git_error_and_the_run_completes(
        self, tmp_path, monkeypatch, capsys, git_prefix, subcommand, outcome,
    ):
        """The skip is per comment: a rerun retries it, and a comment whose git call fails again fails for
        that comment. The message names the subcommand, whichever `-c` settings preceded it."""
        repo, thread = self._fixed_thread(tmp_path)
        self._fail_git_calls(monkeypatch, git_prefix, outcome)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)  # no SystemExit

        assert candidates == []
        stderr = capsys.readouterr().err
        assert "skipped {'git-error': 1, 'reply': 1}" in stderr
        assert f"comment {_COMMENT_ID} skipped, git {subcommand}" in stderr
        assert "1 comment(s) skipped on a git failure, their IDs are listed above" in stderr
        assert "nothing written" not in stderr

    def test_a_comment_whose_git_call_failed_still_leaves_a_written_shortlist(self, tmp_path, monkeypatch, capsys):
        repo, thread = self._fixed_thread(tmp_path)
        self._fail_git_calls(monkeypatch, ("show",), subprocess.TimeoutExpired(["git"], 10))
        fake_gh = _FakeGh(comments=thread, closed_pulls=[_closed_pull(_MERGED_PR_NUMBER, "feat")])
        real_mine = mine_pr_comments.mine
        monkeypatch.setattr(mine_pr_comments, "mine", lambda repo_root: real_mine(repo_root, run=fake_gh))
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        args = run_review_bench.build_parser().parse_args(["mine-pr-comments", "--local-dir", str(local_dir)])

        exit_code = run_review_bench.cmd_mine_pr_comments(args)  # no SystemExit

        assert exit_code == 0
        assert defects.load_candidates(local_dir / "pr_comment_candidates.json") == []
        assert "'git-error': 1" in capsys.readouterr().err

    def test_a_comment_on_a_gitlink_path_is_a_git_error_naming_git_show_and_the_run_completes(
        self, tmp_path, capsys,
    ):
        """`git show <commit>:<submodule path>` exits 128 while the path exists, which no negative answer
        about the comment explains."""
        repo, _main_sha, _introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{'1' * 40},vendored")
        _git(repo, "commit", "-q", "-m", "vendor a submodule")
        gitlink_commit = _git(repo, "rev-parse", "HEAD").strip()
        thread = [_pr_review_comment(gitlink_commit, path="vendored"), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)  # no SystemExit

        assert candidates == []
        stderr = capsys.readouterr().err
        assert "skipped {'git-error': 1, 'reply': 1}" in stderr
        assert f"comment {_COMMENT_ID} skipped, git show exited 128 for a path that exists" in stderr

    def test_a_fatal_status_from_two_commits_that_exist_is_a_git_error_not_unreachable(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, thread = self._fixed_thread(tmp_path)
        self._fail_git_calls(monkeypatch, ("merge-base", "--is-ancestor"), 128)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'git-error': 1" in capsys.readouterr().err

    def test_a_comment_on_a_commit_the_repo_does_not_hold_is_unreachable_not_a_git_error(self, tmp_path, capsys):
        """git exits 128 for an object it lacks, which is a negative answer about the comment."""
        repo, _main_sha, _introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        thread = [_pr_review_comment("f" * 40), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        stderr = capsys.readouterr().err
        assert "skipped {'original-commit-unreachable': 1, 'reply': 1}" in stderr

    def test_a_comment_on_a_path_the_commit_lacks_is_an_anchor_mismatch_not_a_git_error(self, tmp_path, capsys):
        repo, thread = self._fixed_thread(tmp_path, path="missing.py")

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "skipped {'anchor-mismatch': 1, 'reply': 1}" in capsys.readouterr().err

    def test_a_branch_whose_commits_cannot_be_listed_leaves_the_membership_unrecorded_and_says_so(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, thread = self._fixed_thread(tmp_path)
        self._fail_git_calls(monkeypatch, ("rev-list",), 129)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        (candidate,) = candidates
        assert "head_on_pr_branch" not in candidate.evidence
        assert "could not list the commits of refs/heads/feat (git rev-list exited 129)" in capsys.readouterr().err

    def test_a_bidi_override_in_the_branch_name_is_escaped_in_the_listing_failure_message(
        self, tmp_path, monkeypatch, capsys,
    ):
        branch_name = "feat\u202eevil"
        repo, thread = self._fixed_thread(tmp_path)
        _git(repo, "branch", "-m", "feat", branch_name)
        self._fail_git_calls(monkeypatch, ("rev-list",), 129)

        _candidates, _fake_gh = _mine_pr_comments(
            repo, thread, closed_pulls=[_closed_pull(_MERGED_PR_NUMBER, branch_name)],
        )

        stderr = capsys.readouterr().err
        assert "could not list the commits of refs/heads/feat\\u202eevil (git rev-list exited 129)" in stderr
        assert "\u202e" not in stderr

    def test_a_branch_with_no_merge_base_leaves_the_membership_unrecorded(self, tmp_path, monkeypatch):
        repo, thread = self._fixed_thread(tmp_path)
        monkeypatch.setattr(mine_review_rounds, "_merge_base", lambda *args: None)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert [("head_on_pr_branch" in candidate.evidence) for candidate in candidates] == [False]

    def test_a_head_already_contained_in_origin_main_leaves_the_membership_unrecorded(self, tmp_path):
        """The merge-base is the branch tip, so the branch adds no commits of its own: an empty list is
        no evidence the head is outside the PR branch."""
        repo, thread = self._fixed_thread(tmp_path)
        _set_origin_main(repo, _git(repo, "rev-parse", "feat").strip())

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        (candidate,) = candidates
        assert "head_on_pr_branch" not in candidate.evidence

    def test_a_head_that_is_the_repos_root_commit_is_head_unresolved(self, tmp_path, capsys):
        """The commented line blames to the root commit, which has no parent to serve as the base."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        comment = _pr_review_comment(introducing_sha, original_line=1, diff_hunk="@@ -0,0 +1 @@\n+line0")

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        assert candidates == []
        assert "skipped {'head-unresolved': 1, 'reply': 1}" in capsys.readouterr().err

    @pytest.mark.parametrize("path", ["módulo.py", "-dash.py", "dir/a:b.py", "dir with space/x.py"])
    def test_a_legitimate_tracked_path_is_mined_whatever_its_characters(self, tmp_path, monkeypatch, path):
        """Non-ASCII, option-shaped, colon-bearing, and spaced names all reach git behind `--` or a `<sha>:`
        prefix, so none steers an option or a revision."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path, path=path)
        git_commands = self._fail_git_calls(monkeypatch, ("no-such-subcommand",), 0)
        comment = _pr_review_comment(introducing_sha, path=path)

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.evidence["path"] == path
        assert candidate.description == f"{path}:2 — This value is wrong."
        blame_commands = [command for command in git_commands if "blame" in command]
        assert blame_commands and all(command[-2:] == ["--", path] for command in blame_commands)

    @pytest.mark.parametrize(
        "path",
        [
            pytest.param("../outside.py", id="leading-dot-dot"),
            pytest.param("dir/../x.py", id="inner-dot-dot"),
            pytest.param("./app.py", id="leading-dot"),
            pytest.param("/abs/app.py", id="absolute"),
            pytest.param("app.py/", id="trailing-slash"),
            pytest.param("dir//x.py", id="empty-segment"),
            pytest.param("a\x00b.py", id="nul"),
            pytest.param("a\nb.py", id="newline"),
            pytest.param("a\tb.py", id="tab"),
            pytest.param("a\x1b[31mb.py", id="escape"),
            pytest.param("a\u3164b.py", id="invisible-filler"),
            pytest.param("", id="empty"),
            pytest.param(None, id="null"),
        ],
    )
    def test_a_path_that_cannot_name_a_tracked_file_is_malformed_and_reaches_no_git_call(
        self, tmp_path, monkeypatch, capsys, path,
    ):
        repo, thread = self._fixed_thread(tmp_path, path=path)
        git_commands = self._fail_git_calls(monkeypatch, ("no-such-subcommand",), 0)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'malformed': 1" in capsys.readouterr().err
        assert all(path not in argument for command in git_commands for argument in command if path)

    @pytest.mark.parametrize(
        "original_commit_id",
        [
            pytest.param(None, id="null"),
            pytest.param(7, id="integer"),
            pytest.param("abc1234", id="short-sha"),
            pytest.param("--upload-pack=touch /tmp/pwned", id="option-shaped"),
            pytest.param("-" * 40, id="dashes"),
            pytest.param("A" * 40, id="uppercase-hex"),
            pytest.param("g" * 40, id="non-hex"),
            pytest.param("a" * 41, id="forty-one-hex"),
            pytest.param("a" * 40 + "\n", id="trailing-newline"),
        ],
    )
    def test_an_original_commit_id_that_is_not_a_40_hex_sha_is_malformed_and_reaches_no_git_call(
        self, tmp_path, monkeypatch, capsys, original_commit_id,
    ):
        repo, _main_sha, _introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        git_commands = self._fail_git_calls(monkeypatch, ("no-such-subcommand",), 0)
        thread = [_pr_review_comment(original_commit_id), _pr_thread_reply(_fixed_reply_body(fix_sha))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'malformed': 1" in capsys.readouterr().err
        assert not any(
            isinstance(original_commit_id, str) and original_commit_id in argument
            for command in git_commands for argument in command
        )

    @pytest.mark.parametrize(
        ("overrides", "skip_reason"),
        [
            pytest.param({"original_line": True}, "no-line", id="boolean-original-line"),
            pytest.param({"original_line": 0}, "no-line", id="zero-original-line"),
            pytest.param({"original_line": -3}, "no-line", id="negative-original-line"),
            pytest.param({"original_line": "2"}, "no-line", id="string-original-line"),
            pytest.param({"original_line": 2.0}, "no-line", id="float-original-line"),
            pytest.param({"id": True}, "malformed", id="boolean-id"),
            pytest.param({"id": "1001"}, "malformed", id="string-id"),
            pytest.param({"pull_request_url": None}, "malformed", id="no-pull-request-url"),
            pytest.param(
                {"pull_request_url": f"https://api.github.com/repos/{_REPOSITORY}/issues/{_MERGED_PR_NUMBER}"},
                "malformed", id="url-without-a-pulls-segment",
            ),
        ],
    )
    def test_a_comment_whose_line_id_or_pull_request_is_not_a_plain_value_is_skipped_under_its_own_reason(
        self, tmp_path, capsys, overrides, skip_reason,
    ):
        repo, thread = self._fixed_thread(tmp_path, **overrides)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert f"'{skip_reason}': 1" in capsys.readouterr().err

    def test_a_fix_commit_with_no_readable_author_date_is_unresolvable(self, tmp_path, monkeypatch, capsys):
        repo, thread = self._fixed_thread(tmp_path)
        monkeypatch.setattr(mine_pr_comments, "_commit_date", lambda *args: None)

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'fix-sha-unresolvable': 1" in capsys.readouterr().err

    def test_a_reply_sha_that_git_cannot_resolve_to_one_commit_is_unresolvable(self, tmp_path, monkeypatch, capsys):
        """`rev-parse --verify --quiet` exits 1 for an abbreviation that names no commit or several, and
        a 7-hex ambiguity is impractical to build, so the exit status is injected."""
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        real_run = subprocess.run

        def ambiguous_for_the_reply_sha(command, **kwargs):
            if command[1:4] == ["rev-parse", "--verify", "--quiet"] and command[4].startswith(fix_sha[:7]):
                return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"")
            return real_run(command, **kwargs)

        monkeypatch.setattr(subprocess, "run", ambiguous_for_the_reply_sha)
        thread = [_pr_review_comment(introducing_sha), _pr_thread_reply(_fixed_reply_body(fix_sha[:7]))]

        candidates, _fake_gh = _mine_pr_comments(repo, thread)

        assert candidates == []
        assert "'fix-sha-unresolvable': 1" in capsys.readouterr().err

    def test_two_fixed_replies_with_the_same_created_at_resolve_to_the_greater_reply_id(self, tmp_path):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        _write(repo, "app.py", "line0\nbetter_value\n")
        second_fix_sha = _commit_at(repo, "fix again", "2026-01-04T12:00:00+0000")
        same_second = "2026-01-05T00:00:00Z"
        first_reply = _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001, created_at=same_second)
        second_reply = _pr_thread_reply(_fixed_reply_body(second_fix_sha), id=2002, created_at=same_second)

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), second_reply, first_reply])

        assert [candidate.fix_commit for candidate in candidates] == [second_fix_sha]

    def test_a_marked_reply_whose_created_at_does_not_parse_is_dropped_and_counted(self, tmp_path, capsys):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        reply = _pr_thread_reply(_fixed_reply_body(fix_sha), created_at="the fourth of January")

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), reply])

        assert candidates == []
        assert "skipped {'no-agent-reply': 1, 'reply': 1, 'reply-unparseable-created-at': 1}" in capsys.readouterr().err

    def test_an_unparseable_created_at_on_one_reply_leaves_the_threads_other_fixed_reply_in_force(
        self, tmp_path, capsys,
    ):
        repo, _main_sha, introducing_sha, fix_sha = _pr_comment_repo(tmp_path)
        unparseable = _pr_thread_reply(_fixed_reply_body("deadbee"), id=2002, created_at="not a date")
        dated = _pr_thread_reply(_fixed_reply_body(fix_sha), id=2001)

        candidates, _fake_gh = _mine_pr_comments(repo, [_pr_review_comment(introducing_sha), dated, unparseable])

        assert [candidate.fix_commit for candidate in candidates] == [fix_sha]
        assert "'reply-unparseable-created-at': 1" in capsys.readouterr().err

    def test_a_lone_carriage_return_before_the_commented_line_does_not_shift_the_line_numbers(self, tmp_path):
        """Text-mode reads turn a bare CR into a line break, which would put the commented line at 3."""
        repo, main_sha, _introducing_sha, _fix_sha = _pr_comment_repo(tmp_path)
        _git(repo, "checkout", "-q", "-B", "feat", main_sha)
        _write(repo, "app.py", "line0\rcarriage\nbad_value\n")
        introducing_sha = _commit_at(repo, "introduce bad value", "2026-01-02T00:00:00+0000")
        _write(repo, "app.py", "line0\rcarriage\ngood_value\n")
        fix_sha = _commit_at(repo, "fix wrong value", "2026-01-04T00:00:00+0000")
        comment = _pr_review_comment(introducing_sha)

        candidates, _fake_gh = _mine_pr_comments(repo, [comment, _pr_thread_reply(_fixed_reply_body(fix_sha))])

        (candidate,) = candidates
        assert candidate.head_commit == introducing_sha
        assert candidate.description == "app.py:2 — This value is wrong."


class TestClassifyReply:
    _FORTY_HEX = "0123456789abcdef" * 2 + "01234567"

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            pytest.param(_fixed_reply_body(_FORTY_HEX), ("FIXED", _FORTY_HEX), id="fixed-with-a-40-hex-sha"),
            pytest.param(_fixed_reply_body("0123abc"), ("FIXED", "0123abc"), id="fixed-with-a-7-hex-sha"),
            pytest.param(_fixed_reply_body("0123ABC"), ("FIXED", "0123abc"), id="fixed-sha-is-lowercased"),
            pytest.param(
                _fixed_reply_body(_FORTY_HEX).removeprefix("> "), ("FIXED", _FORTY_HEX), id="fixed-without-the-blockquote",
            ),
            pytest.param(_RESPOND_PR_FIXED_EXAMPLE, ("fixed-without-sha", None), id="example-as-printed"),
            pytest.param(_fixed_reply_body(""), ("fixed-without-sha", None), id="sha-value-stripped"),
            pytest.param(_RESPOND_PR_LATEST_COMMIT_REPLY, ("fixed-without-sha", None), id="no-commit-sha-field"),
            pytest.param(_fixed_reply_body("0123ab"), ("fixed-without-sha", None), id="six-hex-sha"),
            pytest.param(_fixed_reply_body(_FORTY_HEX + "0"), ("fixed-without-sha", None), id="forty-one-hex-sha"),
            pytest.param(_fixed_reply_body("0123abcxyz"), ("fixed-without-sha", None), id="sha-with-trailing-garbage"),
            pytest.param(_RESPOND_PR_OUT_OF_SCOPE_REPLY, ("out-of-scope-or-deferred", None), id="out-of-scope"),
            pytest.param(_RESPOND_PR_DEFERRED_REPLY, ("out-of-scope-or-deferred", None), id="deferred"),
            pytest.param(_RESPOND_PR_EXPLAIN_DESIGN_EXAMPLE, ("not-fixed", None), id="explain-design"),
            pytest.param(_RESPOND_PR_AGREE_NO_CHANGE_REPLY, ("not-fixed", None), id="agree-no-change"),
        ],
    )
    def test_a_marked_reply_classifies_by_its_field_tokens(self, body, expected):
        assert mine_pr_comments.classify_reply(body) == expected

    def test_the_example_as_printed_and_its_40_hex_variant_classify_differently(self):
        printed_class, _ = mine_pr_comments.classify_reply(_RESPOND_PR_FIXED_EXAMPLE)
        variant_class, _ = mine_pr_comments.classify_reply(_fixed_reply_body(self._FORTY_HEX))

        assert (printed_class, variant_class) == ("fixed-without-sha", "FIXED")

    def test_the_marker_constant_equals_the_shell_value_it_restates(self):
        library = Path(__file__).resolve().parents[1] / "claude" / ".claude" / "scripts" / "_respond-pr-lib.sh"

        result = subprocess.run(
            ["bash", "-c", 'source "$1" && printf "%s" "$RESPOND_PR_OWNERSHIP_MARKER"', "bash", str(library)],
            capture_output=True, text=True, check=True,
        )

        assert result.stdout == mine_pr_comments.RESPOND_PR_OWNERSHIP_MARKER


class TestCommentedLineText:
    @pytest.mark.parametrize(
        ("diff_hunk", "expected"),
        [
            pytest.param("@@ -1 +1,2 @@\n line0\n+  added_line  ", "added_line", id="added-line"),
            pytest.param("@@ -1 +1,2 @@\n line0\n+added   _line", "added_line", id="interior-whitespace-removed"),
            pytest.param("@@ -1,2 +1,2 @@\n+x\n context_line", "context_line", id="context-line"),
            pytest.param("@@ -1 +1 @@\n+tail\n\\ No newline at end of file", "tail", id="no-newline-marker"),
            pytest.param("@@ -1,2 +1 @@\n line0\n-removed_line", "", id="removed-line-cannot-anchor-the-right-side"),
            pytest.param("", "", id="empty-hunk"),
            pytest.param("@@ -1 +1 @@\n+", "", id="blank-added-line"),
        ],
    )
    def test_returns_the_whitespace_normalized_text_of_the_hunks_last_diff_line(self, diff_hunk, expected):
        assert mine_pr_comments._commented_line_text(diff_hunk) == expected


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


class TestMinePrCommentsParser:
    """The repository comes from `origin`, the author from `gh`, and the host is pinned, so no flag
    names any of them."""

    def test_the_subcommand_takes_only_the_local_dir_flag(self):
        args = run_review_bench.build_parser().parse_args(["mine-pr-comments"])

        assert set(vars(args)) == {"subcommand", "local_dir", "func"}

    @pytest.mark.parametrize(
        "flag",
        ["--repo", "--repository", "--owner", "--author", "--login", "--user", "--host", "--hostname", "--remote", "--token"],
    )
    def test_no_flag_names_a_repository_an_author_or_a_host(self, flag, capsys):
        with pytest.raises(SystemExit) as exit_info:
            run_review_bench.build_parser().parse_args(["mine-pr-comments", flag, "value"])

        assert exit_info.value.code == 2
        assert "unrecognized arguments" in capsys.readouterr().err


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
            "  description:\n    x was left at its stale initial value.\n  path:", "lens: staff-backend-engineer",
            "reviewer_could_have_caught_it: True (unchecked default)",
            "lines_exist_at_introducing_head: False (unchecked default)", "file_is_markdown: False",
        ):
            assert shown in stderr
        assert fix_sha not in stderr  # 12-character SHAs only
        assert stderr.rindex("description:") < stderr.rindex("path:") < stderr.rindex("inclusion fields:")
        assert stderr.rindex("inclusion fields:") < stderr.rindex("[y/N/q]")

    def test_a_pr_comment_candidate_is_not_labeled_unchecked_for_the_line_check_it_computed(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, source="pr-comment", id="pr-comment:1001", lines_exist_at_introducing_head=False,
        )
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert "lines_exist_at_introducing_head: False\n" in stderr
        assert "reviewer_could_have_caught_it: True (unchecked default)" in stderr

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

    @pytest.mark.parametrize("source", ["szz", "review-round", "pr-comment"])
    @pytest.mark.parametrize("candidate_is_markdown", [True, False])
    def test_confirm_writes_the_candidates_evidence_path_and_file_kind_for_every_source(
        self, tmp_path, monkeypatch, source, candidate_is_markdown,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, source=source, file_is_markdown=candidate_is_markdown,
            evidence={"path": "docs/guide.md" if candidate_is_markdown else "src/app.py"},
        )
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        (confirmed,) = defects.load_confirmed_defects(defects_path)
        assert confirmed.path == ("docs/guide.md" if candidate_is_markdown else "src/app.py")
        assert confirmed.file_is_markdown is candidate_is_markdown

    def test_confirm_rejects_a_candidate_with_no_evidence_path_instead_of_writing_it(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, evidence={},
        )
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert defects.load_confirmed_defects(defects_path) == []
        assert "rejected c-1 -- path must be a non-empty string" in capsys.readouterr().err

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

    def test_a_candidate_with_no_description_is_prompted_like_any_other_and_a_y_then_asks_for_a_description(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description="",
        )
        _feed_answers(monkeypatch, "y", "x was left at its stale initial value.")

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert exit_code == 0
        assert stderr.count("[y/N/q]") == 1
        assert stderr.count("one-line description:") == 1
        assert "description: (none" in stderr
        (confirmed,) = defects.load_confirmed_defects(defects_path)
        assert confirmed.description == "x was left at its stale initial value."
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-1")]

    @pytest.mark.parametrize("answer", ["", "n", "N", "yes"])
    def test_a_skipped_undescribed_candidate_never_shows_the_description_prompt(
        self, tmp_path, monkeypatch, capsys, answer,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description="",
        )
        _feed_answers(monkeypatch, answer, "an unconsumed line")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert "one-line description:" not in capsys.readouterr().err
        assert not defects_path.exists()
        assert sys.stdin.read() == "an unconsumed line\n"

    def test_a_y_on_a_candidate_that_already_has_a_description_shows_no_description_prompt(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert "one-line description:" not in capsys.readouterr().err
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-1"]

    @pytest.mark.parametrize("typed_line", ["", "   \t "])
    def test_an_empty_or_whitespace_description_accepts_nothing_and_the_loop_continues(
        self, tmp_path, monkeypatch, typed_line,
    ):
        repo, local_dir, defects_path, _fix_sha = self._undescribed_then_described_candidates(tmp_path, monkeypatch)
        _feed_answers(monkeypatch, "y", typed_line, "y")

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 0

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-described"]
        assert _pin_refs(repo) == [defects.defect_pin_ref("c-described")]

    def test_end_of_input_at_the_description_prompt_accepts_nothing_for_it_and_stops_like_q(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, local_dir, defects_path, _fix_sha = self._undescribed_then_described_candidates(
            tmp_path, monkeypatch, undescribed_first=False,
        )
        _feed_answers(monkeypatch, "y", "y")  # c-described accepted, then c-undescribed gets "y" and end of input

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 0

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-described"]
        assert capsys.readouterr().err.count("one-line description:") == 1

    def test_ctrl_c_at_the_description_prompt_writes_and_pins_nothing(self, tmp_path, monkeypatch, capsys):
        repo, local_dir, defects_path, _fix_sha = self._undescribed_then_described_candidates(
            tmp_path, monkeypatch, undescribed_first=False,
        )
        _feed_answers(monkeypatch, "y", "y")

        class InterruptedAtTheDescription:
            def __init__(self, answers: io.StringIO):
                self.answers = answers

            def readline(self):
                line = self.answers.readline()
                if not line:
                    raise KeyboardInterrupt
                return line

        monkeypatch.setattr(sys, "stdin", InterruptedAtTheDescription(sys.stdin))

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 130

        assert not defects_path.exists()
        assert _pin_refs(repo) == []
        assert "interrupted" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("typed_line", "accepted"),
        [
            pytest.param("a plain description", True, id="plain"),
            pytest.param("tab\tseparated", False, id="tab"),
            pytest.param("carriage\rreturn", False, id="cr"),
            pytest.param("trailing carriage return\r", False, id="trailing-cr"),
            pytest.param("escape \x1b[31m", False, id="esc"),
            pytest.param("bidi \N{RIGHT-TO-LEFT OVERRIDE} override", False, id="bidi-override"),
            pytest.param("joiner \N{ZERO WIDTH JOINER} here", False, id="zero-width-joiner"),
            pytest.param("selector \ufe0f here", False, id="variation-selector"),
            pytest.param("selector \U000e0100 here", False, id="supplementary-variation-selector"),
            pytest.param("filler \N{HANGUL FILLER} here", False, id="hangul-filler"),
            pytest.param("blank \N{BRAILLE PATTERN BLANK} here", False, id="braille-blank"),
            pytest.param("separator \N{LINE SEPARATOR} here", False, id="line-separator"),
            pytest.param("separator \N{PARAGRAPH SEPARATOR} here", False, id="paragraph-separator"),
            pytest.param("private \ue000 use", False, id="private-use"),
            pytest.param("noncharacter \uffff here", False, id="noncharacter"),
            pytest.param("unassigned \u0378 here", True, id="unassigned-outside-the-fixed-ranges"),
            pytest.param("naïve café 日本語 🙂", True, id="printable-non-ascii"),
        ],
    )
    def test_a_typed_description_holding_any_control_character_is_rejected_before_it_is_written(
        self, tmp_path, monkeypatch, capsys, typed_line, accepted,
    ):
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description="",
        )
        _feed_answers(monkeypatch, "y", typed_line)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        written = defects.load_confirmed_defects(defects_path)
        assert bool(written) is accepted
        assert (_pin_refs(repo) != []) is accepted
        if not accepted:
            assert "rejected c-1 -- the description holds a control character" in stderr
        for raw in (
            "\x1b", "\r", "\N{RIGHT-TO-LEFT OVERRIDE}", "\N{ZERO WIDTH JOINER}", "\ufe0f",
            "\N{HANGUL FILLER}", "\N{BRAILLE PATTERN BLANK}", "\N{LINE SEPARATOR}", "\ue000",
        ):
            assert raw not in stderr

    @pytest.mark.parametrize("character", _REJECTED_DESCRIPTION_CHARACTERS)
    def test_a_shortlisted_description_holding_a_rejected_character_is_rejected_by_code_point_and_never_prompted(
        self, tmp_path, monkeypatch, capsys, character,
    ):
        """An edit to a `.local/` shortlist reaches `confirm` as a described candidate, which is rejected
        before any prompt."""
        repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description=f"x was left {character} stale.",
        )
        _feed_answers(monkeypatch, "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert f"rejected c-1 -- description holds the disallowed character U+{ord(character):04X}" in stderr
        assert "promote this candidate?" not in stderr
        assert character not in stderr
        assert not defects_path.exists()
        assert _pin_refs(repo) == []

    def test_a_typed_description_that_fails_provenance_writes_and_pins_nothing_and_names_the_run_and_candidate(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        defects.save_candidates(local_dir / "review_round_candidates.json", [
            Candidate(**_candidate_kwargs(
                id="rr-excerpt", source="review-round", description="x holds a stale value.",
                excerpt="the reviewer found that error handling silently swallows it", **commits,
            )),
            Candidate(**_candidate_kwargs(id="c-undescribed", **commits)),
        ])
        defects_path = tmp_path / "defects.json"
        _feed_answers(monkeypatch, "n", "y", "The reviewer found that error handling silently swallows it.")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert "rejected c-undescribed -- description shares the word run 'the reviewer found that error handling" in stderr
        assert "rr-excerpt's excerpt" in stderr
        assert "swallows it" not in stderr  # no excerpt text past the matched run
        assert not defects_path.exists()
        assert _pin_refs(repo) == []

    def test_a_rejected_typed_description_counts_one_rejection_and_the_loop_goes_on_to_the_next_candidate(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        defects.save_candidates(local_dir / "szz_candidates.json", [
            Candidate(**_candidate_kwargs(
                id="rr-excerpt", description="x holds a stale value.",
                excerpt="the reviewer found that error handling silently swallows it", **commits,
            )),
            Candidate(**_candidate_kwargs(id="c-first", **commits)),
            Candidate(**_candidate_kwargs(id="c-escape", **commits)),
            Candidate(**_candidate_kwargs(id="c-second", **commits)),
        ])
        defects_path = tmp_path / "defects.json"
        _feed_answers(
            monkeypatch, "n",
            "y", "The reviewer found that error handling silently swallows it.",  # provenance rejection
            "y", "escape \x1b[31m here",  # control-character rejection
            "y", "x was left at its stale initial value.",
        )

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert "rejected c-first -- description shares the word run" in stderr
        assert "rejected c-escape -- the description holds a control character" in stderr
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["c-second"]
        assert "confirm: 1 appended, 1 skipped, 2 rejected" in stderr

    def _undescribed_then_described_candidates(
        self, tmp_path, monkeypatch, *, undescribed_first: bool = True,
    ) -> tuple[Path, Path, Path, str]:
        """Returns (repo, local_dir, defects_path, fix_sha) for `c-undescribed` and `c-described`, in the
        presented order the flag selects."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        pair = [
            Candidate(**_candidate_kwargs(id="c-undescribed", **commits)),
            Candidate(**_candidate_kwargs(id="c-described", description="x was left at its stale initial value.", **commits)),
        ]
        defects.save_candidates(local_dir / "szz_candidates.json", pair if undescribed_first else pair[::-1])
        return repo, local_dir, tmp_path / "defects.json", fix_sha

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
                    path="app.py", file_is_markdown=False,
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
                path="app.py", file_is_markdown=False,
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
        # src-1 and c-blank have no description yet, so they await beside the two described candidates.
        assert "4 candidate(s) awaiting the engineer at a terminal, 1 rejected" in stderr
        assert "[y/N/q]" not in stderr
        assert not defects_path.exists()
        assert _pin_refs(repo) == []

    @pytest.mark.parametrize(
        "descriptions", [[], ["The reviewer found that error handling swallows it."]],
        ids=["no-candidates", "only-a-rejected-candidate"],
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
        defects_path = tmp_path / "defects.json"
        # The excerpt holder is already confirmed, so it does not await.
        defects.save_confirmed_defects(defects_path, [ConfirmedDefect(
            id="src-1", source="szz", lens="staff-backend-engineer", fix_date="2026-01-01T00:00:00+00:00",
            description="an earlier confirmed description.", path="app.py", file_is_markdown=False, **commits,
        )])
        _feed_answers(monkeypatch, terminal=False)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 0
        assert "0 candidate(s) awaiting" in capsys.readouterr().err

    def test_without_a_terminal_a_candidate_with_no_description_counts_as_awaiting_and_exits_2(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._repo_with_a_passing_candidate(
            tmp_path, monkeypatch, description="",
        )
        _feed_answers(monkeypatch, "y", terminal=False)

        exit_code = run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert exit_code == 2
        assert "1 candidate(s) awaiting the engineer at a terminal, 0 rejected" in capsys.readouterr().err
        assert not defects_path.exists()

    # --- terminal-unsafe text -----------------------------------------------

    _HOSTILE = "\x1b[31m\r\N{RIGHT-TO-LEFT OVERRIDE}"

    def _assert_hostile_text_only_escaped(self, stderr: str) -> None:
        for raw in ("\x1b", "\r", "\N{RIGHT-TO-LEFT OVERRIDE}"):
            assert raw not in stderr
        for escaped in ("\\x1b", "\\r", "\\u202e"):
            assert escaped in stderr

    def test_escape_carriage_return_and_bidi_override_in_the_commit_subject_show_escaped(
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
            id="c-1", base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha,
            description="x was left at its stale initial value.", evidence={"path": "app.py"},
        ))])
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, tmp_path / "defects.json"))

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "introduce x\\x1b[31m" in stderr  # the subject reached the display

    def test_a_candidate_whose_id_or_path_holds_hostile_text_is_rejected_with_the_text_escaped(
        self, tmp_path, monkeypatch, capsys,
    ):
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        defects.save_candidates(local_dir / "szz_candidates.json", [
            Candidate(**_candidate_kwargs(
                id=f"c-1{self._HOSTILE}", description="x was left at its stale initial value.",
                evidence={"path": "app.py"}, **commits,
            )),
            Candidate(**_candidate_kwargs(
                id="c-2", description="x was left at its stale initial value.",
                evidence={"path": f"app{self._HOSTILE}.py"}, **commits,
            )),
        ])
        _feed_answers(monkeypatch)
        defects_path = tmp_path / "defects.json"

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "id holds the disallowed character U+001B" in stderr
        assert "path holds the disallowed character U+001B" in stderr
        assert "candidate c-1" not in stderr
        assert "candidate c-2" not in stderr
        assert "inclusion fields:" not in stderr
        assert "[y/N/q]" not in stderr
        assert "confirm: 0 appended, 0 skipped, 2 rejected" in stderr
        assert not defects_path.exists()

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
        "field", ["id", "lens", "source", "fix_date", "description", "lines_exist_at_introducing_head",
                  "reviewer_could_have_caught_it", "file_is_markdown"],
    )
    def test_hostile_text_in_any_field_of_the_approval_display_shows_escaped(self, field, capsys):
        candidate = Candidate(**_candidate_kwargs())
        # Sets the value past the validation `id`, `lens`, `source`, `fix_date`, and the mined description get at construction:
        # the display escapes every field it prints, whatever validation stands upstream.
        object.__setattr__(candidate, field, self._HOSTILE)

        run_review_bench._print_candidate_for_approval(candidate, [])

        self._assert_hostile_text_only_escaped(capsys.readouterr().err)

    def test_hostile_text_in_the_path_of_the_approval_display_shows_escaped(self, capsys):
        candidate = Candidate(**_candidate_kwargs(evidence={"path": f"app{self._HOSTILE}.py"}))

        run_review_bench._print_candidate_for_approval(candidate, [])

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "  path: app\\x1b[31m\\r\\u202e.py" in stderr

    def test_the_diff_hunk_shows_one_escaped_line_per_hunk_line(self, capsys):
        hunk = f"@@ -1 +1,2 @@\n line0\n+bad_value{self._HOSTILE}"
        candidate = Candidate(**_candidate_kwargs(evidence={"path": "app.py", "diff_hunk": hunk}))

        run_review_bench._print_candidate_for_approval(candidate, [])

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "  diff_hunk:\n    @@ -1 +1,2 @@\n     line0\n    +bad_value\\x1b[31m" in stderr
        assert "diff_hunk cut" not in stderr

    def test_a_multi_paragraph_description_prints_as_escaped_indented_lines_above_the_path(self, capsys):
        description = f"app.py:2 — first paragraph.\n\nsecond\tparagraph{self._HOSTILE}"
        candidate = Candidate(**_candidate_kwargs(description=description, evidence={"path": "app.py"}))

        run_review_bench._print_candidate_for_approval(candidate, [])

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert (
            "  description:\n    app.py:2 — first paragraph.\n    \n    second\\tparagraph\\x1b[31m\\r\\u202e\n  path: app.py"
        ) in stderr

    def test_a_long_description_prints_in_full_before_the_path_and_hunk_with_the_inclusion_fields_last(self, capsys):
        description = "\n".join(f"paragraph line {index:05d}" for index in range(2000))
        assert len(description) > 30_000
        hunk = "@@ -1 +1,2 @@\n line0\n+the_commented_line"
        candidate = Candidate(**_candidate_kwargs(description=description, evidence={"path": "app.py", "diff_hunk": hunk}))
        same_location_candidate = Candidate(**_candidate_kwargs(id="c-other", source="pr-comment"))
        subjects = [(_SHA_C, "fix the value")]

        run_review_bench._print_candidate_for_approval(candidate, subjects, [(same_location_candidate, "pending")])

        stderr = capsys.readouterr().err
        shown_description = "\n".join(f"    {line}" for line in description.split("\n"))
        assert f"{shown_description}\n  path: app.py" in stderr
        assert "(diff_hunk cut)" not in stderr
        assert (
            stderr.index(shown_description)
            < stderr.index("  path: app.py")
            < stderr.index("  commit subjects:")
            < stderr.index("  diff_hunk:")
            < stderr.index("  also mined from another source at this head, fix, and path:")
            < stderr.index("  inclusion fields:")
        )
        assert stderr.rstrip("\n").split("\n")[-5:] == [
            "  inclusion fields:",
            "    lens: staff-backend-engineer",
            "    lines_exist_at_introducing_head: True (unchecked default)",
            "    reviewer_could_have_caught_it: True (unchecked default)",
            "    file_is_markdown: False",
        ]

    def test_a_diff_hunk_over_the_bound_keeps_its_last_line_and_says_the_start_was_cut(self, capsys):
        """The commented line is the hunk's last, so the cut must fall at the start."""
        bound = run_review_bench._CONFIRM_MAX_DIFF_HUNK_CHARS
        context_lines = [f" context_line_{index:03d}" for index in range(bound // 8)]
        hunk = "\n".join(["@@ -1,90 +1,91 @@", *context_lines, "+the_commented_line"])
        assert len(hunk) > 2 * bound  # the last line sits well past the bound
        candidate = Candidate(**_candidate_kwargs(evidence={"path": "app.py", "diff_hunk": hunk}))

        run_review_bench._print_candidate_for_approval(candidate, [])

        shown_hunk = capsys.readouterr().err.split("  diff_hunk:\n")[1].split("\n  inclusion fields:")[0]
        shown_lines = shown_hunk.split("\n")
        assert shown_lines[0] == "    ... (diff_hunk cut)"
        assert shown_lines[-1] == "    +the_commented_line"
        assert "@@ -1,90" not in shown_hunk
        assert all(line.startswith("     context_line_") for line in shown_lines[1:-1])  # no half-cut first line

    def test_one_hunk_line_over_the_bound_shows_its_tail_and_says_it_was_cut(self, capsys):
        bound = run_review_bench._CONFIRM_MAX_DIFF_HUNK_CHARS
        long_line = "+" + "x" * (bound * 2) + "tail_marker"
        candidate = Candidate(**_candidate_kwargs(evidence={"path": "app.py", "diff_hunk": f"@@ -1 +1 @@\n{long_line}"}))

        run_review_bench._print_candidate_for_approval(candidate, [])

        stderr = capsys.readouterr().err
        assert "x" * bound not in stderr
        assert "xxtail_marker" in stderr
        assert "... (diff_hunk cut)" in stderr

    @pytest.mark.parametrize("evidence", [{"path": "app.py"}, {"path": "app.py", "diff_hunk": ""}, {"diff_hunk": 7}])
    def test_a_candidate_without_a_text_diff_hunk_shows_no_diff_hunk_section(self, evidence, capsys):
        run_review_bench._print_candidate_for_approval(Candidate(**_candidate_kwargs(evidence=evidence)), [])

        assert "diff_hunk" not in capsys.readouterr().err

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

    # --- source order, cross-source annotation, the prompt header, and the head label ---

    def _save_shortlists(self, tmp_path, monkeypatch, shortlists: dict[str, list[dict]]) -> tuple[Path, Path, Path]:
        """Saves one shortlist file per key, each candidate sharing one head, fix, and path. Returns (repo,
        local_dir, defects_path)."""
        repo, introducing_sha, fix_sha = self._repo_with_two_commits(tmp_path)
        monkeypatch.setattr(run_review_bench, "REPO_ROOT", repo)
        local_dir = tmp_path / "local"
        commits = dict(base_commit=introducing_sha, head_commit=introducing_sha, fix_commit=fix_sha)
        for filename, candidate_overrides in shortlists.items():
            defects.save_candidates(
                local_dir / filename,
                [Candidate(**_candidate_kwargs(**commits, **overrides)) for overrides in candidate_overrides],
            )
        return repo, local_dir, tmp_path / "defects.json"

    def _presented_ids(self, stderr: str) -> list[str]:
        return re.findall(r"^candidate (\S+)$", stderr, flags=re.MULTILINE)

    def test_candidates_come_out_review_round_then_pr_comment_then_szz_and_in_miner_order_within_a_source(
        self, tmp_path, monkeypatch, capsys,
    ):
        """Filename order is the reverse of source order here, and one file mixes two sources."""
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "a_candidates.json": [
                dict(id="sz-1", source="szz", description="szz first."),
                dict(id="sz-2", source="szz", description="szz second."),
                dict(id="pc-1", source="pr-comment", description="pr comment first."),
            ],
            "z_candidates.json": [
                dict(id="rr-1", source="review-round", description="review round first."),
                dict(id="pc-2", source="pr-comment", description="pr comment second."),
            ],
        })
        _feed_answers(monkeypatch, *["n"] * 5)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert self._presented_ids(capsys.readouterr().err) == ["rr-1", "pc-1", "pc-2", "sz-1", "sz-2"]

    def test_a_source_outside_the_order_sorts_last(self):
        known = [SimpleNamespace(source=source) for source in ("szz", "pr-comment", "review-round")]
        unknown = SimpleNamespace(source="synthetic")

        ordered = sorted([unknown, *known], key=run_review_bench._confirm_source_rank)

        assert [candidate.source for candidate in ordered] == ["review-round", "pr-comment", "szz", "synthetic"]

    def test_an_early_q_leaves_the_later_sources_unconfirmed(self, tmp_path, monkeypatch):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "szz_candidates.json": [dict(id="sz-1", source="szz", description="szz first.")],
            "review_round_candidates.json": [dict(id="rr-1", source="review-round", description="review round.")],
        })
        _feed_answers(monkeypatch, "y", "q", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["rr-1"]

    def test_a_same_source_pair_sharing_head_fix_and_path_is_not_flagged_and_both_are_prompted(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "szz_candidates.json": [
                dict(id="sz-1", source="szz", description="one distinct comment."),
                dict(id="sz-2", source="szz", description="another distinct comment."),
            ],
        })
        _feed_answers(monkeypatch, "y", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        assert "also mined from another source" not in stderr
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["sz-1", "sz-2"]

    def test_a_cross_source_pair_is_annotated_with_each_status_and_an_n_hides_nothing(
        self, tmp_path, monkeypatch, capsys,
    ):
        """rr-1 is accepted, pc-1 is declined, sz-1 is still pending when rr-1 shows, and pc-old was confirmed
        by an earlier run. Every one stays listed, and the declined pc-1 does not stop sz-1 being prompted."""
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "all_candidates.json": [
                dict(id="rr-1", source="review-round", description="review round comment."),
                dict(id="pc-1", source="pr-comment", description="pr comment, declined."),
                dict(id="sz-1", source="szz", description="szz comment."),
                dict(id="pc-old", source="pr-comment", description="confirmed earlier."),
            ],
        })
        defects.save_confirmed_defects(defects_path, [ConfirmedDefect(
            id="pc-old", source="pr-comment", lens="staff-backend-engineer", base_commit=_SHA_A, head_commit=_SHA_B,
            fix_commit=_SHA_C, fix_date="2026-01-01T00:00:00+00:00", description="confirmed earlier.",
            path="app.py", file_is_markdown=False,
        )])
        _feed_answers(monkeypatch, "y", "n", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        prompts = capsys.readouterr().err.split("\ncandidate ")[1:]
        annotations = {
            prompt.split("\n", 1)[0]: re.findall(r"^    (\S+) \((\S+)\): (.+)$", prompt, flags=re.MULTILINE)
            for prompt in prompts
        }
        assert annotations["rr-1"] == [
            ("pc-1", "pr-comment", "pending"), ("pc-old", "pr-comment", "confirmed"), ("sz-1", "szz", "pending"),
        ]
        assert annotations["pc-1"] == [
            ("rr-1", "review-round", "accepted this run"), ("sz-1", "szz", "pending"),
        ]
        assert annotations["sz-1"] == [
            ("rr-1", "review-round", "accepted this run"), ("pc-1", "pr-comment", "skipped this run"),
            ("pc-old", "pr-comment", "confirmed"),
        ]
        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["pc-old", "rr-1", "sz-1"]

    def test_a_cross_source_candidate_the_checks_rejected_is_annotated_as_rejected_not_pending(
        self, tmp_path, monkeypatch, capsys,
    ):
        """pc-bad is never prompted: its description holds an escape character, which `confirm` rejects."""
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "all_candidates.json": [
                dict(id="rr-1", source="review-round", description="review round comment."),
                dict(id="pc-bad", source="pr-comment", description="rejected \x1b[31m comment."),
                dict(id="sz-1", source="szz", description="szz comment."),
            ],
        })
        _feed_answers(monkeypatch, "n", "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        stderr = capsys.readouterr().err
        prompts = stderr.split("\ncandidate ")[1:]
        annotations = {
            prompt.split("\n", 1)[0]: re.findall(r"^    (\S+) \((\S+)\): (.+)$", prompt, flags=re.MULTILINE)
            for prompt in prompts
        }
        assert annotations["rr-1"] == [("pc-bad", "pr-comment", "rejected by checks"), ("sz-1", "szz", "pending")]
        assert annotations["sz-1"] == [
            ("rr-1", "review-round", "skipped this run"), ("pc-bad", "pr-comment", "rejected by checks"),
        ]

    def test_hostile_text_in_a_cross_source_annotations_id_and_source_shows_escaped(self, capsys):
        shown = Candidate(**_candidate_kwargs(id="c-shown"))
        other = Candidate(**_candidate_kwargs(id=f"c-other{self._HOSTILE}", source="pr-comment"))
        object.__setattr__(other, "source", f"pr-comment{self._HOSTILE}")  # past the construction-time source check

        run_review_bench._print_candidate_for_approval(shown, [], [(other, "pending")])

        stderr = capsys.readouterr().err
        self._assert_hostile_text_only_escaped(stderr)
        assert "also mined from another source at this head, fix, and path:" in stderr

    def test_candidates_at_different_paths_are_not_annotated_as_a_pair(self, tmp_path, monkeypatch, capsys):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "all_candidates.json": [
                dict(id="rr-1", source="review-round", description="review round comment.", evidence={"path": "a.py"}),
                dict(id="sz-1", source="szz", description="szz comment.", evidence={"path": "b.py"}),
            ],
        })
        _feed_answers(monkeypatch, "n", "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert "also mined from another source" not in capsys.readouterr().err

    def test_without_a_terminal_the_awaiting_count_covers_every_candidate_of_a_cross_source_pair(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "all_candidates.json": [
                dict(id="rr-1", source="review-round", description="review round comment."),
                dict(id="sz-1", source="szz", description="szz comment."),
            ],
        })
        _feed_answers(monkeypatch, terminal=False)

        assert run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path)) == 2

        assert "2 candidate(s) awaiting the engineer at a terminal" in capsys.readouterr().err

    def test_the_header_names_the_candidate_count_ctrl_c_discarding_the_run_q_and_one_line_pastes(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path, _fix_sha = self._two_passing_candidates(tmp_path, monkeypatch)
        _feed_answers(monkeypatch)

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        header = capsys.readouterr().err.split("\ncandidate ")[0]
        assert header.startswith("confirm: 2 candidate(s) to review.")
        assert "Ctrl-C discards every answer in this run, so q is the durable exit." in header
        assert "Paste one line at a time" in header

    @pytest.mark.parametrize(
        ("source", "evidence_overrides", "expected_path_line"),
        [
            pytest.param("pr-comment", {"head_on_pr_branch": True}, "path: app.py", id="head-on-the-pr-branch"),
            pytest.param(
                "pr-comment", {"head_on_pr_branch": False}, "path: app.py (head is outside the PR branch)",
                id="head-outside-the-pr-branch",
            ),
            pytest.param("pr-comment", {}, "path: app.py (PR branch membership unknown)", id="key-absent"),
            pytest.param(
                "pr-comment", {"head_on_pr_branch": "yes"}, "path: app.py (PR branch membership unknown)",
                id="key-not-a-boolean",
            ),
            pytest.param("szz", {}, "path: app.py", id="a-source-that-records-no-branch-membership"),
        ],
    )
    def test_the_path_is_labeled_when_a_pr_comment_head_is_outside_the_pr_branch_or_its_membership_is_unknown(
        self, tmp_path, monkeypatch, capsys, source, evidence_overrides, expected_path_line,
    ):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "all_candidates.json": [dict(
                id="c-1", source=source, description="a comment.", evidence={"path": "app.py", **evidence_overrides},
            )],
        })
        _feed_answers(monkeypatch, "n")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        path_lines = [line for line in capsys.readouterr().err.splitlines() if line.startswith("  path: ")]
        assert path_lines == [f"  {expected_path_line}"]

    # --- a pr-comment description may repeat the comment it was mined from ---

    _COMMENT_TEXT = "app.py:2 — alpha bravo charlie delta echo foxtrot golf."
    _EXCERPT_REPEATING_THE_COMMENT = "the finding says alpha bravo charlie delta echo foxtrot golf and more"

    def _comment_and_excerpt_shortlists(self, tmp_path, monkeypatch, *, comment_source: str, description: str):
        return self._save_shortlists(tmp_path, monkeypatch, {
            "review_round_candidates.json": [dict(
                id="rr-excerpt", source="review-round", description="x holds a stale value.",
                excerpt=self._EXCERPT_REPEATING_THE_COMMENT,
            )],
            "comment_candidates.json": [dict(
                id="comment-1", source=comment_source, description=description,
                evidence={"path": "other.py", "public_comment_text": self._COMMENT_TEXT},
            )],
        })

    def test_a_verbatim_pr_comment_description_sharing_a_run_with_an_excerpt_passes_confirm(
        self, tmp_path, monkeypatch,
    ):
        _repo, local_dir, defects_path = self._comment_and_excerpt_shortlists(
            tmp_path, monkeypatch, comment_source="pr-comment", description=self._COMMENT_TEXT,
        )
        _feed_answers(monkeypatch, "n", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert [d.id for d in defects.load_confirmed_defects(defects_path)] == ["comment-1"]

    def test_words_appended_to_a_pr_comment_that_form_an_excerpt_run_are_rejected_naming_the_run(
        self, tmp_path, monkeypatch, capsys,
    ):
        _repo, local_dir, defects_path = self._save_shortlists(tmp_path, monkeypatch, {
            "review_round_candidates.json": [dict(
                id="rr-excerpt", source="review-round", description="x holds a stale value.",
                excerpt="the finding says one two three four five six seven and more",
            )],
            "comment_candidates.json": [dict(
                id="comment-1", source="pr-comment", description=f"{self._COMMENT_TEXT} one two three four five six",
                evidence={"path": "other.py", "public_comment_text": self._COMMENT_TEXT},
            )],
        })
        _feed_answers(monkeypatch, "n", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert "rejected comment-1 -- description shares the word run 'one two three four five six'" in (
            capsys.readouterr().err
        )
        assert not defects_path.exists()

    @pytest.mark.parametrize("source", ["review-round", "szz"])
    def test_a_candidate_outside_the_github_comment_sources_gets_no_exemption_from_the_evidence_field(
        self, tmp_path, monkeypatch, capsys, source,
    ):
        _repo, local_dir, defects_path = self._comment_and_excerpt_shortlists(
            tmp_path, monkeypatch, comment_source=source, description=self._COMMENT_TEXT,
        )
        _feed_answers(monkeypatch, "n", "y")

        run_review_bench.cmd_confirm(_confirm_args(local_dir, defects_path))

        assert "rejected comment-1 -- description shares the word run" in capsys.readouterr().err
        assert not defects_path.exists()
