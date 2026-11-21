"""Tests for the runner provenance the manifest and run records carry, over throwaway git repositories."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import replay_bench
import review_bench
from replay_bench import provenance
from replay_bench.provenance import RunnerProvenance
from review_bench import runner as review_runner
from review_bench.local_git import isolated_git_environment

IDENTITY = ("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *IDENTITY, *args], cwd=repo, check=True, capture_output=True, text=True, env=isolated_git_environment(),
    ).stdout.strip()


def write(repo: Path, relative: str, text: str) -> None:
    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


@pytest.fixture
def runner_repo(tmp_path) -> Path:
    """A committed checkout with a tracked file under `evals/` and one outside it."""
    repo = tmp_path / "runner-checkout"
    repo.mkdir()
    git(repo, "init", "-q")
    write(repo, ".gitignore", "ignored/\n")
    write(repo, "evals/bench.py", "value = 1\n")
    write(repo, "docs/note.md", "note\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


class TestReadRunnerProvenance:
    def test_a_clean_checkout_reports_its_head_and_no_uncommitted_changes(self, runner_repo):
        result = provenance.read_runner_provenance(runner_repo)

        assert result == RunnerProvenance(git(runner_repo, "rev-parse", "HEAD"), False)

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(lambda repo: write(repo, "evals/bench.py", "value = 2\n"), id="tracked-file-edited"),
            pytest.param(lambda repo: write(repo, "evals/replay_bench/new.py", "x = 1\n"), id="untracked-file-added"),
        ],
    )
    def test_an_uncommitted_change_under_evals_sets_the_dirty_flag(self, runner_repo, change):
        change(runner_repo)

        assert provenance.read_runner_provenance(runner_repo).has_uncommitted_changes is True

    def test_a_checkout_hiding_untracked_files_still_reads_a_new_file_under_evals_as_dirty(self, runner_repo):
        """Kills: a status probe without `--untracked-files=all`, which defers to the checkout's own config."""
        git(runner_repo, "config", "status.showUntrackedFiles", "no")
        write(runner_repo, "evals/replay_bench/new.py", "x = 1\n")
        assert git(runner_repo, "status", "--porcelain") == "", "the setting hides the file from a plain status"

        assert provenance.read_runner_provenance(runner_repo).has_uncommitted_changes is True

    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(lambda repo: write(repo, "docs/note.md", "edited\n"), id="tracked-file-outside-evals"),
            pytest.param(lambda repo: write(repo, "evals/ignored/out.txt", "x\n"), id="ignored-file-under-evals"),
        ],
    )
    def test_a_change_the_runner_does_not_run_leaves_the_dirty_flag_unset(self, runner_repo, change):
        write(runner_repo, "evals/.gitignore", "ignored/\n")
        git(runner_repo, "add", "-A")
        git(runner_repo, "commit", "-q", "-m", "ignore")
        change(runner_repo)

        assert provenance.read_runner_provenance(runner_repo).has_uncommitted_changes is False

    def test_a_directory_that_is_no_checkout_records_none_instead_of_raising(self, tmp_path, monkeypatch):
        outside_any_repo = tmp_path / "not-a-checkout"
        outside_any_repo.mkdir()
        # The probe strips the caller's `GIT_*` variables, so the ceiling reaches git only through the probe's own environment.
        monkeypatch.setattr(
            provenance, "isolated_git_environment",
            lambda: {**isolated_git_environment(), "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        )

        result = provenance.read_runner_provenance(outside_any_repo)

        assert result == RunnerProvenance(None, None)

    @pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"])
    def test_a_git_variable_in_the_environment_does_not_redirect_the_probe(self, runner_repo, tmp_path, monkeypatch, variable):
        """Kills: probing with the caller's environment, where `GIT_DIR` makes `rev-parse` answer for another repository."""
        decoy = tmp_path / "decoy-checkout"
        decoy.mkdir()
        git(decoy, "init", "-q")
        write(decoy, "evals/other.py", "value = 9\n")
        git(decoy, "add", "-A")
        git(decoy, "commit", "-q", "-m", "decoy")
        redirect_targets = {
            "GIT_DIR": decoy / ".git", "GIT_WORK_TREE": decoy, "GIT_INDEX_FILE": decoy / ".git" / "index",
        }
        monkeypatch.setenv(variable, str(redirect_targets[variable]))
        # The runner checkout is clean, so a status that follows any of the redirects does not answer `False`.
        result = provenance.read_runner_provenance(runner_repo)

        assert result == RunnerProvenance(git(runner_repo, "rev-parse", "HEAD"), False)
        assert result.commit != git(decoy, "rev-parse", "HEAD")

    def test_a_checkout_with_no_commit_reads_the_status_probe_alone(self, tmp_path):
        """Kills: one `try` around both probes, where the failed `rev-parse` skips the status probe."""
        unborn = tmp_path / "unborn-checkout"
        unborn.mkdir()
        git(unborn, "init", "-q")
        write(unborn, "evals/bench.py", "value = 1\n")

        result = provenance.read_runner_provenance(unborn)

        assert result == RunnerProvenance(None, True)

    def test_a_status_that_fails_leaves_the_commit_readable(self, runner_repo):
        """Kills: one `try` around both probes, where a failed `status` nulls a commit that was read."""
        (runner_repo / ".git" / "index").write_bytes(b"not an index")

        result = provenance.read_runner_provenance(runner_repo)

        assert result == RunnerProvenance(git(runner_repo, "rev-parse", "HEAD"), None)

    @pytest.mark.parametrize(
        "probe_output",
        ["A" * 40, "a" * 39 + "g", "a" * 40 + "\nforged line", "not-a-hash"],
        ids=["uppercase", "non-hex-digit", "embedded-newline", "free-text"],
    )
    def test_a_commit_that_is_not_an_object_name_reads_as_unreadable(self, runner_repo, monkeypatch, probe_output):
        """Kills: storing the probe's stdout unchecked, which writes a manifest and records that `summarize` then
        refuses to load."""
        monkeypatch.setattr(
            review_runner, "_read_environment_field",
            lambda command, **_kwargs: probe_output if "rev-parse" in command else "",
        )

        assert provenance.read_runner_provenance(runner_repo) == RunnerProvenance(None, False)

    def test_the_default_root_is_the_review_runners_own_checkout(self, runner_repo, monkeypatch):
        """Kills: probing the config checkout or the process's working directory instead of the checkout the
        bench's code runs from."""
        monkeypatch.setattr(review_runner, "REPO_ROOT", runner_repo)
        monkeypatch.chdir(runner_repo.parent)

        assert provenance.read_runner_provenance().commit == git(runner_repo, "rev-parse", "HEAD")


class TestTheRunnerSourceLayout:
    def test_both_bench_packages_load_from_under_the_directory_the_dirty_flag_watches(self):
        """A tripwire, not behavioural coverage: `git status -- evals` reads clean for any edit once the packages
        live elsewhere, which would record a dirty checkout as clean."""
        watched = (review_runner.REPO_ROOT / provenance.RUNNER_SOURCE_DIR).resolve()

        for package in (replay_bench, review_bench):
            assert Path(package.__file__).resolve().is_relative_to(watched), package.__name__


class TestManifestRoundTrip:
    def test_a_provenance_round_trips_through_the_manifest_fields(self):
        original = RunnerProvenance("a" * 40, True)

        assert RunnerProvenance.from_manifest(original.manifest_fields()) == original

    def test_unreadable_values_round_trip_as_none(self):
        original = RunnerProvenance(None, None)

        assert RunnerProvenance.from_manifest(original.manifest_fields()) == original

    @pytest.mark.parametrize(
        "manifest",
        [
            pytest.param({}, id="neither-key"),
            pytest.param({"runner_commit": "a" * 40}, id="no-dirty-key"),
        ],
    )
    def test_a_manifest_missing_a_key_reads_as_not_recorded(self, manifest):
        assert RunnerProvenance.from_manifest(manifest) is None

    @pytest.mark.parametrize(
        "bad_commit",
        [
            pytest.param(5, id="not-text"),
            pytest.param(["a" * 40], id="a-list"),
            pytest.param("abc", id="abbreviated"),
            pytest.param("A" * 40, id="uppercase"),
            pytest.param("a" * 40 + "\nforged line", id="newline-after-a-hash"),
            pytest.param("g" * 40, id="not-hex"),
        ],
    )
    def test_a_commit_that_is_not_a_full_object_name_reads_as_unreadable(self, bad_commit):
        reading = RunnerProvenance.from_manifest({"runner_commit": bad_commit, "runner_dirty": True})

        assert reading == RunnerProvenance(None, True)

    def test_a_dirty_flag_that_is_not_a_bool_reads_as_unreadable(self):
        reading = RunnerProvenance.from_manifest({"runner_commit": "a" * 40, "runner_dirty": "yes"})

        assert reading == RunnerProvenance("a" * 40, None)

    @pytest.mark.parametrize("object_name", ["a" * 40, "0123456789abcdef" * 4])
    def test_sha1_and_sha256_object_names_are_accepted(self, object_name):
        assert provenance.is_object_name(object_name)


class TestDescribe:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (RunnerProvenance("abc", False), "runner commit abc"),
            (RunnerProvenance("abc", True), "runner commit abc, with uncommitted changes under evals/"),
            (RunnerProvenance("abc", None), "runner commit abc, uncommitted changes under evals/ unreadable"),
            (RunnerProvenance(None, None), "runner commit unreadable"),
            (RunnerProvenance(None, True), "runner commit unreadable, with uncommitted changes under evals/"),
            (RunnerProvenance(None, False), "runner commit unreadable, without uncommitted changes under evals/"),
            (None, "runner commit not recorded"),
        ],
    )
    def test_each_state_reads_distinctly(self, value, expected):
        assert provenance.describe(value) == expected


class TestDescribeCampaign:
    CLEAN_A = RunnerProvenance("a" * 40, False)
    DIRTY_A = RunnerProvenance("a" * 40, True)
    CLEAN_B = RunnerProvenance("b" * 40, False)

    def test_records_that_agree_with_the_manifest_describe_it_once(self):
        assert provenance.describe_campaign(self.CLEAN_A, [self.CLEAN_A, self.CLEAN_A]) == f"runner commit {'a' * 40}"

    def test_a_record_at_another_commit_names_every_reading_with_its_sources(self):
        clause = provenance.describe_campaign(self.CLEAN_A, [self.CLEAN_A, self.CLEAN_B, self.CLEAN_B, self.CLEAN_B])

        assert clause == (
            "runner readings differ across the campaign: "
            f"runner commit {'a' * 40} (manifest, records: 1); runner commit {'b' * 40} (records: 3)"
        )

    def test_a_record_that_differs_only_in_the_dirty_flag_counts_as_another_reading(self):
        clause = provenance.describe_campaign(self.CLEAN_A, [self.DIRTY_A])

        assert clause.startswith("runner readings differ across the campaign: ")
        assert f"runner commit {'a' * 40}, with uncommitted changes under evals/ (records: 1)" in clause

    def test_records_with_no_reading_beside_a_reading_are_counted_as_a_reading_of_their_own(self):
        clause = provenance.describe_campaign(self.CLEAN_A, [self.CLEAN_A, provenance.NO_READING, provenance.NO_READING])

        assert clause == (
            "runner readings differ across the campaign: "
            f"no runner reading (records: 2); runner commit {'a' * 40} (manifest, records: 1)"
        )

    def test_records_with_no_reading_under_a_manifest_with_no_reading_do_not_hide_the_one_commit_that_was_read(self):
        clause = provenance.describe_campaign(None, [self.CLEAN_A, provenance.NO_READING])

        assert clause == (
            f"runner readings differ across the campaign: no runner reading (records: 1); "
            f"runner commit {'a' * 40} (records: 1)"
        )

    def test_a_manifest_reading_alone_beside_records_with_no_reading_is_not_the_whole_campaign(self):
        clause = provenance.describe_campaign(self.CLEAN_A, [provenance.NO_READING])

        assert clause == (
            f"runner readings differ across the campaign: no runner reading (records: 1); runner commit {'a' * 40} (manifest)"
        )

    def test_a_campaign_with_no_manifest_reading_and_records_that_differ_names_them(self):
        clause = provenance.describe_campaign(None, [self.CLEAN_A, self.CLEAN_B])

        assert clause == (
            "runner readings differ across the campaign: "
            f"runner commit {'a' * 40} (records: 1); runner commit {'b' * 40} (records: 1)"
        )

    def test_records_that_agree_describe_themselves_when_the_manifest_holds_no_usable_reading(self):
        assert provenance.describe_campaign(provenance.NO_READING, [self.CLEAN_A]) == f"runner commit {'a' * 40}"

    def test_a_campaign_with_no_reading_anywhere_says_not_recorded(self):
        assert provenance.describe_campaign(None, [provenance.NO_READING]) == "runner commit not recorded"

    def test_a_manifest_whose_probe_failed_and_records_with_no_reading_say_unreadable_not_not_recorded(self):
        clause = provenance.describe_campaign(provenance.NO_READING, [provenance.NO_READING])

        assert clause == "runner commit unreadable"


class TestDescribeResume:
    def test_the_recorded_reading_alone_when_this_checkout_matches(self):
        reading = RunnerProvenance("a" * 40, False)

        assert provenance.describe_resume(reading, reading) == f"runner commit {'a' * 40}"

    def test_both_readings_when_this_checkout_differs(self):
        clause = provenance.describe_resume(RunnerProvenance("a" * 40, False), RunnerProvenance("b" * 40, True))

        assert clause == (
            f"runner commit {'a' * 40} at the first start; this resume runs "
            f"runner commit {'b' * 40}, with uncommitted changes under evals/"
        )

    def test_a_manifest_with_no_reading_is_not_compared(self):
        assert provenance.describe_resume(None, RunnerProvenance("b" * 40, False)) == "runner commit not recorded"
