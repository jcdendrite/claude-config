"""Unit tests for _lib.sh's _lib_capped_for and _lib_status_consistent_with_cap_kill."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ._lib_sh_helpers import _LIB_SH, _SKILL_MANAGEMENT_PLUGIN_LIB, _run_lib_call


# The one cap-boundary test that runs against the real timeout(1) with nothing interposed, so the suite keeps
# end-to-end evidence that the binary itself enforces a cap.
@pytest.mark.timing
def test_lib_capped_for_enforces_cap_when_timeout_present(tmp_path: Path) -> None:
    """timeout(1) on PATH, no gtimeout: _lib_capped_for kills a hung command at the given cap, exit 124."""
    import shutil

    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    sleep_path = shutil.which("sleep")
    if not sleep_path:
        pytest.skip("sleep not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "timeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "sleep").symlink_to(sleep_path)
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    start = time.monotonic()
    result = _run_lib_call("_lib_capped_for 1 sleep 5", env=env)
    elapsed = time.monotonic() - start

    assert result.returncode == 124, repr(result)
    assert elapsed < 3, f"capped sleep took {elapsed:.1f}s — the timeout branch did not fire"


# A fake timeout(1) on this PATH would win _lib_capped_for's first probe, so the gtimeout branch under test
# would never execute and the exit-124 assertion would still pass.
@pytest.mark.timing
def test_lib_capped_for_enforces_cap_via_gtimeout_when_timeout_absent(tmp_path: Path) -> None:
    """timeout(1) absent, gtimeout(1) present (Homebrew coreutils naming): _lib_capped_for still enforces the cap."""
    import shutil

    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available to alias as gtimeout — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    sleep_path = shutil.which("sleep")
    if not sleep_path:
        pytest.skip("sleep not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    # Alias the real timeout binary under the gtimeout name and omit timeout
    # from PATH entirely, simulating a Homebrew-coreutils-only machine.
    (tmp_path / "gtimeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "sleep").symlink_to(sleep_path)
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    start = time.monotonic()
    result = _run_lib_call("_lib_capped_for 1 sleep 5", env=env)
    elapsed = time.monotonic() - start

    assert result.returncode == 124, repr(result)
    assert elapsed < 3, f"capped sleep took {elapsed:.1f}s — the gtimeout branch did not fire"


# Both binaries are absent on purpose: a fake timeout(1) here would cap the call and invert the uncapped result this asserts.
def test_lib_capped_for_runs_uncapped_when_neither_timeout_nor_gtimeout_present(
    tmp_path: Path,
) -> None:
    """Neither timeout(1) nor gtimeout(1) on PATH: _lib_capped_for runs the command uncapped, not denied."""
    import shutil

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    sleep_path = shutil.which("sleep")
    if not sleep_path:
        pytest.skip("sleep not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "sleep").symlink_to(sleep_path)
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    start = time.monotonic()
    # seconds (0.2) is well under the sleep duration (0.6) — a real cap would
    # kill this early, so completing at the full duration proves it ran uncapped.
    result = _run_lib_call("_lib_capped_for 0.2 sleep 0.6", env=env)
    elapsed = time.monotonic() - start

    assert result.returncode == 0, repr(result)
    assert elapsed >= 0.6, f"sleep finished in {elapsed:.2f}s — a cap fired despite neither binary being present"


def test_lib_capped_for_uncapped_fallback_emits_stderr_note(tmp_path: Path) -> None:
    """Neither timeout(1) nor gtimeout(1) on PATH: _lib_capped_for's uncapped
    fallback emits a diagnostic naming the gap, so a caller or log can tell
    the cap silently didn't apply rather than reading a clean exit as capped."""
    import shutil

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    # `true` is a bash builtin, so it needs no PATH entry of its own.
    result = _run_lib_call("_lib_capped_for 0.1 true", env=env)

    assert result.returncode == 0, repr(result)
    assert "_lib_capped_for" in result.stderr and "uncapped" in result.stderr, repr(result.stderr)


def test_lib_capped_for_uncapped_fallback_note_fires_every_call(tmp_path: Path) -> None:
    """The note above fires on every uncapped-fallback call, not just the
    first -- each call independently ran without a cap, so each is worth
    its own diagnostic."""
    import shutil

    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    result = _run_lib_call(
        "_lib_capped_for 0.1 true; _lib_capped_for 0.1 true; _lib_capped_for 0.1 true", env=env,
    )

    assert result.returncode == 0, repr(result)
    assert result.stderr.count("_lib_capped_for: neither timeout nor gtimeout") == 3, repr(result.stderr)


# Probe order is the subject: a fake timeout(1) here would be the binary that wins, so the test would prove
# the fake was preferred rather than the real one.
def test_lib_capped_for_prefers_timeout_over_gtimeout_when_both_present(tmp_path: Path) -> None:
    """Both timeout(1) and gtimeout(1) on PATH: _lib_capped_for dispatches to the real timeout(1) first.

    A fake gtimeout stands in for a swapped probe order — if _lib_capped_for
    ever checked gtimeout before timeout, this test would observe the fake's
    distinct output instead of the real command's.
    """
    import shutil

    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    printf_path = shutil.which("printf")
    if not printf_path:
        pytest.skip("printf not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "timeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "printf").symlink_to(printf_path)
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    (tmp_path / "dirname").symlink_to(dirname_path)

    fake_gtimeout = tmp_path / "gtimeout"
    fake_gtimeout.write_text("#!/bin/bash\nprintf 'GTIMEOUT_WAS_USED'\nexit 99\n")
    fake_gtimeout.chmod(0o755)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    result = _run_lib_call("_lib_capped_for 1 printf real-timeout-path", env=env)

    assert result.returncode == 0, repr(result)
    assert result.stdout == "real-timeout-path", repr(result.stdout)


@pytest.mark.parametrize("lib_path", [_LIB_SH, _SKILL_MANAGEMENT_PLUGIN_LIB], ids=["stowed", "plugin"])
@pytest.mark.parametrize("binary_name", ["timeout", "gtimeout"])
def test_lib_capped_for_passes_kill_after_flag_before_the_duration(
    tmp_path: Path, lib_path: Path, binary_name: str
) -> None:
    """`-k` and `2` arrive as two separate argv entries, in that order, both
    ahead of SECONDS: timeout(1) requires OPTION before DURATION, and
    BusyBox's timeout rejects the long `--kill-after=2` spelling. Pinned
    against both binary names (the gtimeout case leaves `timeout` absent from
    PATH) and both copies of the wrapper, so a later "tidy-up" to
    `--kill-after=2` fails this test instead of shipping."""
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    argv_log = tmp_path / "argv.log"
    fake_timeout = tmp_path / binary_name
    fake_timeout.write_text(
        "#!/bin/bash\n"
        f"printf '%s\\n' \"$@\" > {shlex.quote(str(argv_log))}\n"
        "exit 0\n"
    )
    fake_timeout.chmod(0o755)
    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    result = subprocess.run(
        ["bash", "-c", f". {shlex.quote(str(lib_path))}; _lib_capped_for 5 echo hi"],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, repr(result)
    argv = argv_log.read_text().splitlines()
    assert argv[:4] == ["-k", "2", "5", "echo"], (
        f"expected -k and 2 as separate argv entries ahead of the duration, got {argv!r}"
    )


# A plain, untrapped `sleep N` (this block's sibling cases above) dies to a bare SIGTERM either way, so it
# cannot show whether `-k` is present. The SIGTERM-immune child is described in
# conftest's `_write_conditional_sleep_shim`; only `-k`'s grace-expiry SIGKILL can end it.
@pytest.mark.timing
def test_lib_capped_for_kills_a_sigterm_immune_child_via_the_grace(tmp_path: Path) -> None:
    """A child that ignores SIGTERM outright must still return within
    cap+grace, not hang to the fixture's own much longer sleep. The grace's
    SIGKILL reports 137, not timeout(1)'s ordinary 124, so the wall time is
    asserted alongside the status."""
    timeout_path = shutil.which("timeout")
    if not timeout_path:
        pytest.skip("timeout(1) not available — BSD/macOS without coreutils")
    bash_path = shutil.which("bash")
    if not bash_path:
        pytest.skip("bash not found in PATH")
    sleep_path = shutil.which("sleep")
    if not sleep_path:
        pytest.skip("sleep not found in PATH")
    dirname_path = shutil.which("dirname")
    if not dirname_path:
        pytest.skip("dirname not found in PATH")

    (tmp_path / "timeout").symlink_to(timeout_path)
    (tmp_path / "bash").symlink_to(bash_path)
    (tmp_path / "sleep").symlink_to(sleep_path)
    # dirname is required too: _lib.sh's own sourcing of _config.sh
    # resolves its path via `$(dirname "${BASH_SOURCE[0]}")`, and a failed
    # source now aborts _lib.sh's own sourcing entirely (see _lib.sh's
    # header comment on that source line).
    (tmp_path / "dirname").symlink_to(dirname_path)

    env = {"PATH": str(tmp_path), "HOME": str(tmp_path)}
    start = time.monotonic()
    result = _run_lib_call("_lib_capped_for 1 bash -c 'trap \"\" TERM; exec sleep 30'", env=env)
    elapsed = time.monotonic() - start

    assert result.returncode == 137, repr(result)
    # Nominal 3s (cap 1 + grace 2). The bound sits well inside the fixture's 30s sleep,
    # so it separates a fired grace from a hang, and its headroom over nominal absorbs subprocess-spawn contention.
    assert elapsed < 15, f"SIGTERM-immune child took {elapsed:.1f}s — the -k grace did not fire"


@pytest.mark.parametrize("cap_kill_status", ["124", "137", "143"])
def test_status_consistent_with_cap_kill_accepts_cap_kill_statuses(cap_kill_status: str) -> None:
    result = _run_lib_call(f"_lib_status_consistent_with_cap_kill {cap_kill_status}", env=dict(os.environ))
    assert result.returncode == 0, repr(result)
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize("other_status", ["0", "1", "2", "125", "126", "127", "130"])
def test_status_consistent_with_cap_kill_rejects_other_statuses(other_status: str) -> None:
    result = _run_lib_call(f"_lib_status_consistent_with_cap_kill {other_status}", env=dict(os.environ))
    assert result.returncode == 1, repr(result)
    assert result.stdout == "" and result.stderr == ""


@pytest.mark.parametrize(
    "call",
    [
        "_lib_status_consistent_with_cap_kill",
        "_lib_status_consistent_with_cap_kill ''",
        "_lib_status_consistent_with_cap_kill abc",
    ],
    ids=["absent", "empty", "non-numeric"],
)
def test_status_consistent_with_cap_kill_rejects_absent_empty_and_non_numeric_under_set_u(call: str) -> None:
    """Returns exactly 1 with no output, and a shell under `set -u` reaches the next statement."""
    result = _run_lib_call(f"set -u; {call}; echo reached-$?", env=dict(os.environ))
    assert result.returncode == 0, repr(result)
    assert result.stdout == "reached-1\n", repr(result.stdout)
    assert result.stderr == ""


def _cap_fire_path_env(tmp_path: Path) -> dict:
    """A PATH holding only the real timeout(1) and the tools _lib.sh and the composition cases need."""
    for name in ("timeout", "bash", "sleep", "dirname"):
        real_path = shutil.which(name)
        if not real_path:
            pytest.skip(f"{name} not found in PATH")
        (tmp_path / name).symlink_to(real_path)
    return {"PATH": str(tmp_path), "HOME": str(tmp_path)}


@pytest.mark.timing
def test_status_consistent_with_cap_kill_recognizes_a_real_sigterm_cap_fire(tmp_path: Path) -> None:
    env = _cap_fire_path_env(tmp_path)
    result = _run_lib_call(
        "_lib_capped_for 1 sleep 5; _lib_status_consistent_with_cap_kill $?; echo classified-$?", env=env
    )
    assert result.stdout == "classified-0\n", repr(result)


@pytest.mark.timing
def test_status_consistent_with_cap_kill_recognizes_a_real_sigterm_immune_cap_fire(tmp_path: Path) -> None:
    env = _cap_fire_path_env(tmp_path)
    result = _run_lib_call(
        "_lib_capped_for 1 bash -c 'trap \"\" TERM; exec sleep 30'; "
        "_lib_status_consistent_with_cap_kill $?; echo classified-$?",
        env=env,
    )
    assert result.stdout == "classified-0\n", repr(result)


def test_status_consistent_with_cap_kill_rejects_a_child_that_exits_one(tmp_path: Path) -> None:
    env = _cap_fire_path_env(tmp_path)
    result = _run_lib_call(
        "_lib_capped_for 5 bash -c 'exit 1'; _lib_status_consistent_with_cap_kill $?; echo classified-$?",
        env=env,
    )
    assert result.stdout == "classified-1\n", repr(result)


def test_lib_capped_for_aborts_on_unset_seconds_argument() -> None:
    """An empty or unset SECONDS -- e.g. _lib_capped_for "$UNSET_VAR" cmd --
    hard-aborts the sourcing script via bash's ${1:?msg} rather than falling
    through to run the command uncapped, per _lib_capped_for's own header
    comment in _lib.sh."""
    harness = (
        f'. {_LIB_SH}; '
        '_lib_capped_for "$UNSET_VAR" echo should-not-run; '
        'echo SHOULD_NOT_REACH'
    )
    result = subprocess.run(
        ["bash", "-c", harness],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0, repr(result)
    assert "SHOULD_NOT_REACH" not in result.stdout, repr(result.stdout)
    assert "should-not-run" not in result.stdout, repr(result.stdout)
    assert "_lib_capped_for requires a seconds argument" in result.stderr, repr(result.stderr)
