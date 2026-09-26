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

# Imported as a module, not by name: binding these test_*-named functions
# directly at this module's scope would make pytest collect them a second
# time under this file's own node-ID, alongside test_lib.py's.
from . import test_lib
from .test_deny_pii_in_commits import DENY_PII_IN_COMMITS_HOOK, DIFF_CALL_PREDICATE, GHP_TOKEN, _stage


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


def _install_argv_echoing_real_timeout(monkeypatch, tmp_path: Path) -> None:
    """Prepend a fake `timeout` to PATH that prints every argument it was
    handed, exits 124 when its last argument is "__KILL__" (SIGTERM cap
    kill), 143 when it is "__BUSYBOX_KILL__" (BusyBox SIGTERM cap kill), 137
    when it is "__SIGKILL__" (SIGKILL grace), 128 when it is
    "__PLAIN_NONZERO__" (an ordinary nonzero status, git's fatal 128), and 0
    otherwise."""
    fake_real_timeout_dir = tmp_path / "fake-real-timeout"
    fake_real_timeout_dir.mkdir()
    fake_timeout = fake_real_timeout_dir / "timeout"
    fake_timeout.write_text(
        "#!/bin/bash\n"
        "printf '%s\\n' \"$@\"\n"
        'last="${@: -1}"\n'
        '[ "$last" = "__KILL__" ] && exit 124\n'
        '[ "$last" = "__BUSYBOX_KILL__" ] && exit 143\n'
        '[ "$last" = "__SIGKILL__" ] && exit 137\n'
        '[ "$last" = "__PLAIN_NONZERO__" ] && exit 128\n'
        "exit 0\n"
    )
    fake_timeout.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_real_timeout_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize(
    ("last_argument", "expected_returncode", "expected_fired"),
    [
        ("true", 0, Counter()),
        ("__KILL__", 124, Counter({"5 __KILL__": 1})),
        ("__SIGKILL__", 137, Counter({"5 __SIGKILL__": 1})),
        ("__BUSYBOX_KILL__", 143, Counter({"5 __BUSYBOX_KILL__": 1})),
        ("__PLAIN_NONZERO__", 128, Counter()),
    ],
    ids=[
        "completing",
        "status-124-sigterm-kill",
        "status-137-sigkill-after-grace",
        "status-143-busybox-sigterm-kill",
        "status-128-plain-nonzero-completes",
    ],
)
def test_shim_scales_a_leading_kill_after_pair_and_logs_the_status(
    monkeypatch, tmp_path, last_argument, expected_returncode, expected_fired
):
    """The shim strips a leading `-k <n>` pair before the duration-scaling
    check runs, scales it by the same divisor, and re-attaches it ahead of
    the scaled duration. A completing invocation is logged in both started and
    completed; each cap-kill status (124 or 143 from a SIGTERM cap kill, 137
    from the SIGKILL grace) is logged as started without completed. A plain
    nonzero status outside that set (128) is logged as completed, so an
    ordinary failing call is never counted as a cap firing."""
    _install_argv_echoing_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    result = subprocess.run(
        [str(bin_dir / "timeout"), "-k", "2", "5", last_argument], capture_output=True, text=True, check=False
    )

    assert result.returncode == expected_returncode, repr(result)
    assert result.stdout.splitlines()[:3] == ["-k", "0.666", "1.666"], (
        f"expected the scaled grace ahead of the scaled duration, got {result.stdout!r}"
    )
    assert caps_that_fired(bin_dir) == expected_fired


def test_shim_forwards_a_regex_rejected_duration_behind_a_kill_after_pair_unscaled(monkeypatch, tmp_path):
    """A regex-rejected duration behind a `-k <n>` prefix reaches the real
    binary with its grace intact."""
    _install_argv_echoing_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    passthrough = subprocess.run(
        [str(bin_dir / "timeout"), "-k", "2", "019", "true"], capture_output=True, text=True, check=False
    )

    assert passthrough.stdout.splitlines()[:3] == ["-k", "2", "019"], (
        f"a regex-rejected duration behind a -k prefix must still reach the real binary "
        f"with its grace intact, got {passthrough.stdout!r}"
    )


@pytest.mark.parametrize("raw_grace", ["08", "0.5", "2s", "0", "abc"])
def test_shim_forwards_a_non_integer_kill_after_grace_unscaled(monkeypatch, tmp_path, raw_grace):
    """Only a 1-9-leading integer grace is scaled: any other grace reaches
    the real binary as given, while the duration still scales and is still
    logged."""
    _install_argv_echoing_real_timeout(monkeypatch, tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    assert write_scaled_timeout_shim(bin_dir) is True

    result = subprocess.run(
        [str(bin_dir / "timeout"), "-k", raw_grace, "5", "true"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, repr(result)
    assert result.stdout.splitlines()[:3] == ["-k", raw_grace, "1.666"], (
        f"grace {raw_grace!r} should pass through unscaled ahead of the scaled duration, got {result.stdout!r}"
    )
    assert result.stderr == "", f"a rejected grace must not reach bash arithmetic, got stderr {result.stderr!r}"


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
        test_lib.test_timeout_absent_fallback_valid_payload_returns_ok,
        test_lib.test_lib_capped_for_enforces_cap_when_timeout_present,
        test_lib.test_lib_capped_for_enforces_cap_via_gtimeout_when_timeout_absent,
        test_lib.test_lib_capped_for_runs_uncapped_when_neither_timeout_nor_gtimeout_present,
        test_lib.test_lib_capped_for_prefers_timeout_over_gtimeout_when_both_present,
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
        staying <= 4; see the comment above the git_timeout_shim call below.

        `fake_output` makes the diff shim's completion observable: the hook
        allows on the credential-free shim output and denies on the real
        staged diff. The hook's other 5s-capped `git` calls also complete, so
        the assert_cap_engaged message alone cannot tell a completed diff
        shim from a predicate that never matched."""
        # sleep_seconds=1 only completes under the cap while TIMEOUT_SCALE_DIVISOR <= 4
        # (ceil(1/divisor) vs 5/divisor) -- a future divisor raise past 4 must re-derive this.
        env = git_timeout_shim(DIFF_CALL_PREDICATE, fake_output="+no credential here", sleep_seconds=1)
        self._stage_credential(git_repo)
        with (
            pytest.raises(AssertionError, match="completed on its own"),
            assert_cap_engaged(tmp_path, production_cap=5),
        ):
            decision = run_hook(
                DENY_PII_IN_COMMITS_HOOK, bash_input("git commit -m wip"), cwd=git_repo, extra_env=env
            )
        assert decision == "allow", "the diff shim's sleep branch did not run, so the real staged diff was scanned"

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
        env = git_timeout_shim(DIFF_CALL_PREDICATE)
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
        env = git_timeout_shim(DIFF_CALL_PREDICATE)
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

    def test_no_production_cap_sums_kills_across_every_duration(self, monkeypatch, tmp_path):
        """The `elif production_cap is None` branch sums fired_delta across
        every duration and command instead of one pinned duration -- every
        other call site pins production_cap explicitly, so this summation
        path has no coverage without this test. Two kills at different
        durations (5 and 2) must both land in the sum -- a same-duration pair
        couldn't distinguish summing-across-durations from merely counting
        one duration twice: 2 kills pass under killed_calls=2 and raise under
        the default killed_calls=1, proving the summation counts exactly
        rather than merely detecting that something, somewhere, fired."""
        _install_recording_real_timeout(monkeypatch, tmp_path)
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        assert write_scaled_timeout_shim(bin_dir) is True
        shim = str(bin_dir / "timeout")

        def _fire_both_caps():
            subprocess.run([shim, "5", "__KILL__"], check=False)
            subprocess.run([shim, "2", "__KILL__"], check=False)

        with pytest.raises(AssertionError), assert_cap_engaged(bin_dir):
            _fire_both_caps()

        with assert_cap_engaged(bin_dir, killed_calls=2):
            _fire_both_caps()

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
