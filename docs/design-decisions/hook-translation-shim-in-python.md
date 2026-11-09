# Hook translation shim: Python over bash+jq

*2026-11-07.*

The Cursor adapter's translation shim is written in Python, not bash+jq. A bash+jq shim was built first and timed against a Python version of the same common allow path. The Python version had the lower mean, median and p95 on every row measured. Its p99 was higher on one row, the 1.4 MB translate-only path (367.5 ms against 236.9 ms, from 20 runs). The cause of the gap, its growth with payload size and the one higher p99 was not profiled.

## What was measured

The bash+jq shim ran about 4 processes per translate-only run and about 17 counted processes per run with a hook, plus the hook script's own.

The test used 100 runs per path, interleaved (shim, Python, shim, Python, ...) so machine load affected both alike. It ran on one macOS laptop, with Python 3.15 from Homebrew and Homebrew `jq`. A review round was running on the same machine, so the load average was about 10. Medians are more trustworthy than p99 under that load. Each figure is wall time of a subprocess call in milliseconds, so it includes fork and exec. The harness and per-run data behind the tables are not kept in this repository.

Small payload (a one-line Shell command):

| Path | mean | median | p95 | p99 |
|---|---|---|---|---|
| bash+jq shim, translate only | 111.4 | 106.8 | 140.2 | 241.6 |
| Python, translate only | 52.5 | 46.1 | 62.6 | 196.9 |
| bash+jq shim, with a trivial hook | 276.9 | 252.1 | 400.4 | 665.2 |
| Python, with a trivial hook | 67.0 | 62.9 | 87.5 | 141.2 |
| floor: `python3 -c pass` | 38.2 | 32.2 | 49.7 | 184.5 |
| floor: `python3 -I -c pass` | 34.7 | 32.9 | 46.7 | 72.1 |
| floor: `jq -n .` | 9.2 | 8.2 | 12.6 | 38.8 |

A 1.4 MB payload (a 1.4 MB command string; 20 runs, so p95 and p99 rest on one or two samples):

| Path | mean | median | p95 | p99 |
|---|---|---|---|---|
| bash+jq shim, translate only | 133.0 | 129.2 | 142.7 | 236.9 |
| Python, translate only | 72.4 | 56.3 | 67.9 | 367.5 |
| bash+jq shim, with a trivial hook | 1351.6 | 1326.9 | 1545.6 | 2101.7 |
| Python, with a trivial hook | 115.6 | 88.7 | 268.2 | 415.4 |

## What it shows

- `jq` itself starts in about 8 ms, and the Python interpreter in about 32 ms (the table's medians).
- With a trivial hook, the median was 252.1 ms for bash+jq against 62.9 ms for Python at the small payload, and 1326.9 ms against 88.7 ms at 1.4 MB.
- The gap grew with payload size.

## Limits of the comparison

- The Python version covered the common allow path only. It omitted the directory pin, full install-record validation, the `timeout` wrapper, output size caps, most tool translations, the log line and the real hook-output mapping. The Python figures are therefore somewhat optimistic. The added in-process work was not measured.
- It was one pass on one machine under load.

## Does this apply to other hooks in this toolkit

Not measured. By the startup medians above (`jq` 8.2 ms, interpreter 32.2 ms), a hook that starts one or two helpers would not repay the interpreter's start. The inference, not a measurement, is that Python pays off once a hook would start more than about three processes, or copies a large payload through several of them. Measure a hook before porting it.

## Alternatives set aside

- **Keep bash+jq and cut processes.** The bash shim wrapped each step in `timeout`, which accounted for 7 of its 17 counted processes. Removing steps removes the caps they provide.
- **Accept the cost.** The accepted budget is 2 s at the 95th percentile of added hook time per call. With a trivial hook, the bash shim's 1.4 MB p95 was 1545.6 ms and its p99 was 2101.7 ms (20 runs), against 268.2 ms and 415.4 ms for Python.

## Costs of the Python choice

The Python shim's inner deadline bounds only the read from Cursor and the hook run. Its other in-process steps include the parse, the `lstat` and `realpath` of path resolution, the `stat` of a Write's target and the reads of its own config files. They have no time bound, where the bash shim capped every step with `timeout`. Only Cursor's own kill ends a stuck step, and a fail-closed entry blocks on that kill. A Python parse also removes the bash-to-jq separator and NUL-byte limits that made the bash shim refuse some inputs.
