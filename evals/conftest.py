"""Shared fixtures for the evals/ tests."""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

# The signals `run_review_bench.main()` and `measure_subagent_model_resolution.main()` install handlers for.
_TERMINATION_SIGNALS = (signal.SIGHUP, signal.SIGTERM)


@pytest.fixture(autouse=True)
def restore_process_wide_state():
    """Both CLIs' `main()` set the termination-signal handlers and the umask
    for the whole process, so every test that calls one must leave both as it
    found them."""
    saved_handlers = {signum: signal.getsignal(signum) for signum in _TERMINATION_SIGNALS}
    saved_umask = os.umask(0)
    os.umask(saved_umask)
    yield
    for signum, handler in saved_handlers.items():
        if handler is not None:  # None marks a handler installed outside Python, which signal.signal cannot restore
            signal.signal(signum, handler)
    os.umask(saved_umask)


@pytest.fixture(autouse=True)
def isolate_review_bench_from_the_host(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch):
    """Moves `run_review_bench`'s `.local/` default paths under an absent tmp
    directory, so `main()` never touches the host's `.local/`. It also stubs
    `review_bench.runner.default_live_checkout_roots` to return no roots, but
    only when that module is already imported. A test that needs the real
    roots re-installs the function itself."""
    run_review_bench = sys.modules.get("run_review_bench")
    if run_review_bench is None:  # the test module under way never imported it
        return
    real_local_dir = run_review_bench.DEFAULT_LOCAL_DIR
    absent_local_dir = tmp_path_factory.mktemp("review-bench-host") / "local"
    for name, value in list(vars(run_review_bench).items()):
        if name.startswith("DEFAULT_") and isinstance(value, Path) and value.is_relative_to(real_local_dir):
            monkeypatch.setattr(run_review_bench, name, absent_local_dir / value.relative_to(real_local_dir))
    review_bench_runner = sys.modules.get("review_bench.runner")
    if review_bench_runner is not None:
        monkeypatch.setattr(review_bench_runner, "default_live_checkout_roots", lambda: ())
