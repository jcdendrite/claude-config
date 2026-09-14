"""Tests for the scaled `timeout` shim (helpers.py's write_scaled_timeout_shim
and friends) that every cap-boundary test now installs in place of a real
wall-clock wait.

Three concerns:
  - Bidirectional coverage of the shim itself: an integer duration scales
    correctly, a rejected $1 passes through unscaled and records nothing,
    and the started/completed logs discriminate a kill from a completion.
  - A source-scanning tripwire for the five test_lib.py tests that must
    never receive this shim (TestProtectedProbeOrderTestsAreUnscaled).
  - A committed negative control for the discriminator itself
    (TestCapMarkersDetectNonFiringCap).
"""
from __future__ import annotations

import inspect
import os
import shutil
import subprocess
import time
from collections import Counter
from pathlib import Path

import pytest
from helpers import (
    assert_cap_engaged,
    assert_cap_not_engaged,
    bash_input,
    caps_that_fired,
    run_hook,
    scaled_cap,
    scaled_shim_sleep,
    scaled_under_cap_sleep,
    write_scaled_timeout_shim,
)

from .test_deny_pii_in_commits import DENY_PII_IN_COMMITS_HOOK, GHP_TOKEN, _stage
from .test_lib import (
    test_lib_capped_for_enforces_cap_via_gtimeout_when_timeout_absent,
    test_lib_capped_for_enforces_cap_when_timeout_present,
    test_lib_capped_for_prefers_timeout_over_gtimeout_when_both_present,
    test_lib_capped_for_runs_uncapped_when_neither_timeout_nor_gtimeout_present,
    test_timeout_absent_fallback_valid_payload_returns_ok,
)


def _install_recording_real_timeout(monkeypatch, tmp_path: Path) -> None:
    """Prepend a fake `timeout` to PATH standing in for the real
    timeout(1)/gtimeout(1) binary write_scaled_timeout_shim resolves --
    prints the scaled duration it was handed (so a test can pin the exact
    value), and exits 124 when its last argument is the literal sentinel
    "__KILL__", 0 otherwise -- so a test can pin exact started/completed
    log behavior with no real sleep or kill involved."""
    fake_real_timeout_dir = tmp_path / "fake-real-timeout"
    fake_real_timeout_dir.mkdir()
    fake_timeout = fake_real_timeout_dir / "timeout"
    fake_timeout.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$1\"\n"
        'last="${@: -1}"\n'
        '[ "$last" = "__KILL__" ] && exit 124\n'
        "exit 0\n"
    )
    fake_timeout.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_real_timeout_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.timing
def test_scaled_shim_kills_a_real_hung_command_and_records_both_directions(tmp_path):
    """End-to-end against the genuine timeout(1)/gtimeout(1) binary (ledger
    row 5, both directions): a killed invocation records its duration in `started`
    and not `completed`, while a completing one records it in both, leaving
    caps_that_fired unchanged."""
    if not shutil.which("timeout") and not shutil.which("gtimeout"):
        pytest.skip("neither timeout(1) nor gtimeout(1) available — BSD/macOS without coreutils")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    start = time.monotonic()
    killed = subprocess.run(
        [str(bin_dir / "timeout"), "5", "sleep", "3"], capture_output=True, text=True, check=False
    )
    elapsed = time.monotonic() - start
    assert killed.returncode == 124, repr(killed)
    assert elapsed < 2.5, f"the scaled ~1.67s cap should kill `sleep 3` well before the full 3s, took {elapsed:.2f}s"
    assert caps_that_fired(bin_dir) == Counter({"5 sleep": 1})

    completed = subprocess.run(
        [str(bin_dir / "timeout"), "5", "true"], capture_output=True, text=True, check=False
    )
    assert completed.returncode == 0, repr(completed)
    assert caps_that_fired(bin_dir) == Counter({"5 sleep": 1}), (
        "a completing invocation must record its duration in both started and "
        "completed, leaving the fired count unchanged"
    )


def test_duration_scaling_produces_exact_values(monkeypatch, tmp_path):
    """The emitted duration string for each production cap tier, verified
    against a recording fake so no real timeout(1) run is needed."""
    _install_recording_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    for production, expected in [("5", "1.666"), ("2", "0.666"), ("10", "3.333"), ("15", "5.000")]:
        result = subprocess.run(
            [str(bin_dir / "timeout"), production, "anything"], capture_output=True, text=True, check=False
        )
        assert result.stdout.strip() == expected, (production, result.stdout)


@pytest.mark.parametrize("raw_arg", ["019", "0", "2.5", "", "abc", "-5"])
def test_regex_rejected_inputs_pass_through_unscaled_and_record_nothing(monkeypatch, tmp_path, raw_arg):
    """`019` is the regression case ledger row 9 names (bash octal landmine); `0`,
    `2.5`, an empty $1, a non-numeric $1, and a negative $1 round out the
    regex-boundary enumeration. All six take the same unscaled passthrough
    and leave no marker record."""
    _install_recording_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    result = subprocess.run(
        [str(bin_dir / "timeout"), raw_arg, "marker"], capture_output=True, text=True, check=False
    )
    assert result.stdout.strip() == raw_arg, f"{raw_arg!r} should pass through to the real binary unscaled"
    assert caps_that_fired(bin_dir) == Counter(), (
        f"{raw_arg!r} should record nothing — the regex rejects it before any log write"
    )


def test_multiple_invocations_are_counted_not_overwritten(monkeypatch, tmp_path):
    """Three invocations of which one is killed leave caps_that_fired() ==
    {"5 __KILL__": 1} — the append-and-count behavior a 21-call hook run
    depends on."""
    _install_recording_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True
    shim = str(bin_dir / "timeout")

    subprocess.run([shim, "5", "true"], check=False)
    subprocess.run([shim, "5", "__KILL__"], check=False)
    subprocess.run([shim, "5", "true"], check=False)

    assert caps_that_fired(bin_dir) == Counter({"5 __KILL__": 1})


def test_assert_cap_not_engaged_passes_on_completion_and_raises_on_kill(monkeypatch, tmp_path):
    """assert_cap_not_engaged is the inverse of assert_cap_engaged, for the
    one inverted site (ledger row 7) whose shim sleep must finish inside
    its cap: it must pass when the shim ran and nothing was killed, and
    raise when it was."""
    _install_recording_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True
    shim = str(bin_dir / "timeout")

    with assert_cap_not_engaged(bin_dir, production_cap=5):
        subprocess.run([shim, "5", "true"], check=False)

    with pytest.raises(AssertionError), assert_cap_not_engaged(bin_dir, production_cap=5):
        subprocess.run([shim, "5", "__KILL__"], check=False)


def test_scaled_shim_sleep_always_outlasts_its_tier_cap():
    """scaled_shim_sleep never returns a value at or below its tier's
    scaled cap — the identity `sleep > cap` a single divisor preserves."""
    assert scaled_shim_sleep(10) > scaled_cap(5)
    assert scaled_shim_sleep(20) > scaled_cap(15)
    assert scaled_shim_sleep(30) > scaled_cap(15)
    assert scaled_shim_sleep(3.5) > scaled_cap(2)


def test_scaled_under_cap_sleep_stays_below_its_cap():
    """The one inverted site (ledger row 7): its sleep must finish inside its
    cap, not outlast it."""
    assert scaled_under_cap_sleep(3.5) < scaled_cap(10)


class TestProtectedProbeOrderTestsAreUnscaled:
    """These five tests must never receive the scaled shim — it would
    silently defeat the absent/probe-order condition they're named for.
    `inspect.getsource` only checks each function's own literal source, not
    its call graph, so this is a tripwire, not a guarantee."""

    _PROTECTED_FUNCTIONS = (
        test_timeout_absent_fallback_valid_payload_returns_ok,
        test_lib_capped_for_enforces_cap_when_timeout_present,
        test_lib_capped_for_enforces_cap_via_gtimeout_when_timeout_absent,
        test_lib_capped_for_runs_uncapped_when_neither_timeout_nor_gtimeout_present,
        test_lib_capped_for_prefers_timeout_over_gtimeout_when_both_present,
    )

    @pytest.mark.parametrize("fn", _PROTECTED_FUNCTIONS, ids=lambda fn: fn.__name__)
    def test_protected_function_source_has_no_scaling_call(self, fn):
        source = inspect.getsource(fn)
        for forbidden in ("write_scaled_timeout_shim", "scaled_shim_sleep", "TIMEOUT_SCALE_DIVISOR"):
            assert forbidden not in source, (
                f"{fn.__name__} references {forbidden!r} — this test's own premise is "
                f"that no scaled timeout(1) shim exists on its PATH (ledger row 4); routing it "
                f"through the shared scaling helper defeats that premise"
            )


class TestCapMarkersDetectNonFiringCap:
    """Committed negative control for the discriminator, covering every
    cap-boundary site since they share one shim body. Reuses
    test_deny_pii_in_commits.py's 5s-cap staged-diff scenario so a failing
    leg indicts the discriminator, not an untested fixture."""

    @staticmethod
    def _stage_credential(git_repo):
        _stage(git_repo, "f.txt", f"x\ntoken {GHP_TOKEN}\n")

    @pytest.mark.timing
    def test_completed_not_killed_raises(self, isolated_home, git_repo, git_timeout_shim, tmp_path):
        """sleep_seconds=1 still installs the scaled timeout shim
        (`_write_conditional_sleep_shim` only skips installation at
        sleep_seconds=0, the fast-return shape test_marker_script.py:1083,1123
        use) but scales to well under the 5s cap's ~1.67s, so the capped
        call completes on its own rather than being killed. assert_cap_engaged
        must raise, with a message distinct from the never-invoked case below.
        This "completes, not killed" outcome depends on TIMEOUT_SCALE_DIVISOR
        staying <= 4; see the comment above the git_timeout_shim call below."""
        # sleep_seconds=1 only completes under the cap while TIMEOUT_SCALE_DIVISOR <= 4
        # (ceil(1/divisor) vs 5/divisor) -- a future divisor raise past 4 must re-derive this.
        env = git_timeout_shim('[ "$1" = "diff" ]', sleep_seconds=1)
        self._stage_credential(git_repo)
        with (
            pytest.raises(AssertionError, match="completed on its own"),
            assert_cap_engaged(tmp_path, production_cap=5),
        ):
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)

    def test_never_invoked_raises_with_a_distinct_message(self, tmp_path):
        """The fail-closed property's own proof: a bin_dir where no shim
        ever ran must raise with a message naming the never-invoked case,
        not the completed case above — no wall-clock floor could
        distinguish these two."""
        with (
            pytest.raises(AssertionError, match="never invoked"),
            assert_cap_engaged(tmp_path, production_cap=5),
        ):
            pass

    @pytest.mark.timing
    def test_wrong_production_cap_raises_while_correct_cap_passes(
        self, isolated_home, git_repo, git_timeout_shim, tmp_path
    ):
        """The committed replacement for CUMULATIVE_DIFF_CAP_FLOOR_SECONDS's
        regression guarantee: a real 5s-cap kill must be rejected under the
        wrong production_cap and accepted under the right one."""
        env = git_timeout_shim('[ "$1" = "diff" ]')
        self._stage_credential(git_repo)

        with pytest.raises(AssertionError), assert_cap_engaged(tmp_path, production_cap=15):
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)

        with assert_cap_engaged(tmp_path, production_cap=5):
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)

    @pytest.mark.timing
    def test_killed_calls_count_must_match_exactly(
        self, isolated_home, git_repo, git_timeout_shim, tmp_path
    ):
        """The one property the three row-8 chained-call sites depend on
        with no other coverage: killed_calls compares exactly, not merely
        truthy."""
        env = git_timeout_shim('[ "$1" = "diff" ]')
        self._stage_credential(git_repo)

        def _run_twice():
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)
            run_hook(DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env)

        with pytest.raises(AssertionError), assert_cap_engaged(tmp_path, production_cap=5, killed_calls=1):
            _run_twice()

        with assert_cap_engaged(tmp_path, production_cap=5, killed_calls=2):
            _run_twice()

        with pytest.raises(AssertionError), assert_cap_engaged(tmp_path, production_cap=5, killed_calls=3):
            _run_twice()

    def test_command_attribution_distinguishes_which_stage_of_a_same_duration_pipe_was_killed(
        self, monkeypatch, tmp_path
    ):
        """The property command-attributed marker logging exists to prove:
        two same-duration capped stages of a pipe (mirroring
        _lib_active_bypass_marker_live's `cat | tr`) are distinguished by
        command, not merged into one duration-only count.
        assert_cap_engaged(command=...) must attribute a kill to the stage
        that was actually killed, in either direction."""
        _install_recording_real_timeout(monkeypatch, tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        assert write_scaled_timeout_shim(bin_dir) is True
        shim = str(bin_dir / "timeout")

        with assert_cap_engaged(bin_dir, production_cap=5, killed_calls=1, command="cat"):
            subprocess.run([shim, "5", "cat", "__KILL__"], check=False)
            subprocess.run([shim, "5", "tr", "true"], check=False)
        with (
            pytest.raises(AssertionError),
            assert_cap_engaged(bin_dir, production_cap=5, killed_calls=1, command="tr"),
        ):
            subprocess.run([shim, "5", "cat", "__KILL__"], check=False)
            subprocess.run([shim, "5", "tr", "true"], check=False)

        with assert_cap_engaged(bin_dir, production_cap=5, killed_calls=1, command="tr"):
            subprocess.run([shim, "5", "cat", "true"], check=False)
            subprocess.run([shim, "5", "tr", "__KILL__"], check=False)
        with (
            pytest.raises(AssertionError),
            assert_cap_engaged(bin_dir, production_cap=5, killed_calls=1, command="cat"),
        ):
            subprocess.run([shim, "5", "cat", "true"], check=False)
            subprocess.run([shim, "5", "tr", "__KILL__"], check=False)
