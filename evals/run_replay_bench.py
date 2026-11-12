#!/usr/bin/env python3
"""Replay bench CLI: replays a source repo's past commits as coding tasks under two configuration arms and
counts the comment and docstring lines each arm adds.

Subcommands:
- `tasks`: mines a source repo's first-parent commits into a task set. It spends nothing.
- `preflight`: probes each task set's source repos for their Claude Code account. It launches nothing.
- `smoke`: runs one block of one run per arm on one task, and writes the account's smoke-pass record.
- `run`: runs the campaign block by block, under spend stops. It needs a passing `smoke` per account.
- `summarize`: prints the paired arm difference over a campaign's complete blocks to the terminal only.

Local only, never CI: every run is a paid `claude -p` session. `.local/` holds task sets, run records and diffs.
The design lives in .claude/plans/docstring-verbosity-experiment.md. Exit codes: 0 done, 1 a run that stopped
short, 2 a refusal. Needs Python 3.12 or newer and git 2.32 or newer.
"""
from __future__ import annotations

import argparse
import contextlib
import math
import os
import re
import signal
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import fields
from pathlib import Path

import measure_subagent_model_resolution as msmr
from replay_bench import account as account_mod
from replay_bench import arms as arms_mod
from replay_bench import campaign, smoke, summary, tasks
from replay_bench import runner as replay_runner
from replay_bench.render import SafeStream
from review_bench import runner as review_runner
from review_bench.identifiers import InvalidIdentifierError, validate_campaign_id
from review_bench.local_git import isolated_git_environment
from review_bench.runner import HarnessInvalidatedError, RunStoreLocked

EVALS_DIR = Path(__file__).resolve().parent
DEFAULT_LOCAL_DIR = EVALS_DIR / "replay_bench" / ".local"
LOCAL_DIR_NAME = ".local"
MIN_PYTHON = (3, 12)
MIN_GIT = (2, 32)
TASKS_DIR_NAME = "tasks"
_TASK_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{12}")
_GIT_VERSION_RE = re.compile(r"git version (\d+)\.(\d+)")
DEFAULT_SEED = 0


def _positive_float(text: str) -> float:
    """NaN is refused too: every comparison against it is False, so a NaN stop would never halt."""
    value = float(text)
    if not math.isfinite(value) or value <= 0:
        raise argparse.ArgumentTypeError(f"must be a finite number > 0, got {value}")
    return value


def _flag(field_name: str) -> str:
    return "--" + field_name.replace("_", "-")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--local-dir", default=None, help="Where task sets, records and diffs live. Default: evals/replay_bench/.local/.",
    )
    parser.add_argument("--fixture-git-timeout-s", type=_positive_float, default=replay_runner.FIXTURE_GIT_TIMEOUT_S,
                        help="Bounds every git call that builds a fixture or reads it after a run.")
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    p_tasks = subparsers.add_parser("tasks", help="Mine a source repo's commits into a task set.")
    p_tasks.add_argument(
        "--source-repo", action="append", required=True, help="The top level of a local git repository. Repeatable.",
    )
    p_tasks.add_argument("--trusted-author", action="append", required=True, help="An author email to keep. Repeatable.")
    p_tasks.add_argument("--name", required=True, help="The task set's name.")
    for limit_field in fields(tasks.SelectionLimits):
        limit_type = str if limit_field.name == "rev" else int
        p_tasks.add_argument(_flag(limit_field.name), type=limit_type, default=None, help=f"Default: {limit_field.default}.")
    p_tasks.set_defaults(func=cmd_tasks)

    p_preflight = subparsers.add_parser("preflight", help="Probe each source repo's account; launches nothing.")
    p_preflight.add_argument("--tasks", required=True, help="The task set's name.")
    _add_preflight_flags(p_preflight)
    p_preflight.set_defaults(func=cmd_preflight)

    p_smoke = subparsers.add_parser("smoke", help="Run one block of one run per arm on one task.")
    p_smoke.add_argument("--tasks", required=True)
    p_smoke.add_argument("--task-id", required=True)
    _add_launch_flags(p_smoke)
    p_smoke.set_defaults(func=cmd_smoke)

    p_run = subparsers.add_parser("run", help="Run the campaign block by block.")
    p_run.add_argument("--tasks", required=True)
    p_run.add_argument("--k", type=int, required=True, help="Runs per arm per task.")
    p_run.add_argument("--cumulative-stop-usd", type=_positive_float, required=True)
    p_run.add_argument(
        "--account-stop-usd", type=_positive_float, default=None, help="Per account. Default: the cumulative stop.",
    )
    p_run.add_argument("--allow-early-stop", action="store_true", help="Accept a campaign ceiling above the cumulative stop.")
    p_run.add_argument("--campaign-id", default=None, help="Resume a campaign by passing its ID again.")
    p_run.add_argument("--arm", action="append", choices=sorted(arms_mod.ARMS), default=None, help="Default: every arm.")
    p_run.add_argument("--seed", type=int, default=DEFAULT_SEED)
    _add_launch_flags(p_run)
    p_run.set_defaults(func=cmd_run)

    p_summarize = subparsers.add_parser("summarize", help="Print a campaign's summary to the terminal.")
    p_summarize.add_argument("--campaign-id", required=True)
    p_summarize.add_argument("--budget-cap-usd", type=_positive_float, default=msmr.PER_RUN_BUDGET_CAP_USD)
    p_summarize.set_defaults(func=cmd_summarize)
    return parser


def _add_preflight_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--direnv-timeout-s", type=_positive_float, default=None,
                        help="Bounds each repo's direnv probe. Default: the review bench's environment-read timeout.")


def _add_launch_flags(parser: argparse.ArgumentParser) -> None:
    _add_preflight_flags(parser)
    parser.add_argument("--model-id", default=review_runner.REVIEWER_MODEL_ID)
    parser.add_argument("--budget-cap-usd", type=_positive_float, default=msmr.PER_RUN_BUDGET_CAP_USD)
    parser.add_argument("--timeout-s", type=int, default=review_runner.REVIEWER_TIMEOUT_S)


# --- subcommands ---------------------------------------------------------------


def cmd_tasks(args: argparse.Namespace, local_dir: Path) -> int:
    given_limits = {limit.name: getattr(args, limit.name) for limit in fields(tasks.SelectionLimits)}
    limits = tasks.SelectionLimits(**{name: value for name, value in given_limits.items() if value is not None})
    tasks.task_set_path(local_dir / TASKS_DIR_NAME, args.name)  # validates the name before any mining
    report = tasks.mine_tasks([Path(repo) for repo in args.source_repo], args.trusted_author, limits)
    path = tasks.write_task_set(local_dir / TASKS_DIR_NAME, args.name, report.tasks)
    print("\n".join(tasks.format_mining_summary(report)))
    print(f"wrote {len(report.tasks)} tasks to {str(path)!r}")
    return 0


def _preflight_repos(args: argparse.Namespace, repos: Sequence[str]) -> dict[str, account_mod.Account]:
    accounts = {}
    for repo in repos:
        account = account_mod.preflight_repo(Path(repo), direnv_timeout_s=args.direnv_timeout_s)
        accounts[repo] = account
        dropped = ", ".join(repr(name) for name in account.dropped_variables) or "none"
        print(
            f"{repo!r}: config dir {str(account.config_dir)!r}, probe {account.probe_seconds:.1f} s; "
            f"variables the launch drops: {dropped}"
        )
    return accounts


def cmd_preflight(args: argparse.Namespace, local_dir: Path) -> int:
    task_list = tasks.read_task_set(local_dir / TASKS_DIR_NAME, args.tasks)
    _preflight_repos(args, sorted({task.source_repo for task in task_list}))
    return 0


def cmd_smoke(args: argparse.Namespace, local_dir: Path) -> int:
    if not _TASK_ID_RE.fullmatch(args.task_id):
        raise HarnessInvalidatedError(f"--task-id {args.task_id!r} does not match <8 hex>-<12 hex>")
    task_list = tasks.read_task_set(local_dir / TASKS_DIR_NAME, args.tasks)
    matching = [task for task in task_list if task.id == args.task_id]
    if not matching:
        raise HarnessInvalidatedError(f"task set {args.tasks!r} holds no task {args.task_id!r}")
    task = matching[0]
    account = _preflight_repos(args, [task.source_repo])[task.source_repo]
    config = smoke.SmokeConfig(
        model_id=args.model_id, budget_cap_usd=args.budget_cap_usd, timeout_s=args.timeout_s,
        fixture_git_timeout_s=args.fixture_git_timeout_s, local_dir=local_dir,
    )
    return smoke.run_smoke(config, account, task, runner_environ=os.environ, launch=msmr._run_claude_to_completion)


def cmd_run(args: argparse.Namespace, local_dir: Path) -> int:
    if args.k < 1:
        raise HarnessInvalidatedError(f"--k must be at least 1, got {args.k}")
    arm_names = tuple(args.arm or arms_mod.DEFAULT_ARM_NAMES)
    if len(set(arm_names)) != len(arm_names):
        raise HarnessInvalidatedError(f"--arm names an arm more than once: {list(arm_names)}")
    # Stored on the namespace so an interrupt's resume hint names the ID in force, given or generated.
    args.campaign_id = campaign_id = validate_campaign_id(args.campaign_id or f"run-{replay_runner.new_campaign_suffix()}")
    task_list = tasks.read_task_set(local_dir / TASKS_DIR_NAME, args.tasks)
    if not task_list:
        raise HarnessInvalidatedError(f"task set {args.tasks!r} holds no tasks")
    accounts_by_repo = _preflight_repos(args, sorted({task.source_repo for task in task_list}))
    config = campaign.CampaignConfig(
        campaign_id=campaign_id, tasks_name=args.tasks, tasks=tuple(task_list),
        arm_names=arm_names, k=args.k, seed=args.seed, model_id=args.model_id,
        budget_cap_usd=args.budget_cap_usd, timeout_s=args.timeout_s, cumulative_stop_usd=args.cumulative_stop_usd,
        account_stop_usd=args.account_stop_usd or args.cumulative_stop_usd, allow_early_stop=args.allow_early_stop,
        fixture_git_timeout_s=args.fixture_git_timeout_s, local_dir=local_dir,
    )
    print(f"campaign {campaign_id}")
    return campaign.run_campaign(
        config, accounts_by_repo, runner_environ=os.environ, launch=msmr._run_claude_to_completion,
    )


def cmd_summarize(args: argparse.Namespace, local_dir: Path) -> int:
    validate_campaign_id(args.campaign_id)
    lines = summary.summarize(
        summary.campaign_dir_for(local_dir, args.campaign_id), local_dir=local_dir, budget_cap_usd=args.budget_cap_usd,
    )
    print("\n".join(lines))
    return 0


# --- startup -------------------------------------------------------------------


class _SignalInterrupt(KeyboardInterrupt):
    """A KeyboardInterrupt that remembers which signal raised it, so `main()` can exit 128 plus its number."""

    def __init__(self, signum: int) -> None:
        super().__init__()
        self.signum = signum


def _raise_signal_interrupt(signum: int, _frame) -> None:
    raise _SignalInterrupt(signum)


def _refuse_old_git() -> None:
    try:
        completed = subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=False,
            timeout=review_runner._ENVIRONMENT_READ_TIMEOUT_S,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        detail = review_runner._os_error_detail(exc) if isinstance(exc, OSError) else "timed out"
        raise HarnessInvalidatedError(f"git is not runnable: {detail}") from None
    match = _GIT_VERSION_RE.match(completed.stdout)
    if completed.returncode != 0 or match is None or (int(match[1]), int(match[2])) < MIN_GIT:
        raise HarnessInvalidatedError(
            f"git {'.'.join(map(str, MIN_GIT))} or newer is required, because isolated_git_environment's "
            "GIT_CONFIG_GLOBAL needs it"
        )


def check_local_dir(local_dir: Path) -> None:
    """Raises HarnessInvalidatedError, naming the path, unless it is absolute, equal to its own resolved
    path, named `.local`, and ignored by the work tree that holds it. It creates nothing."""
    shown = repr(str(local_dir))
    if not local_dir.is_absolute():
        raise HarnessInvalidatedError(f"--local-dir {shown} is not an absolute path")
    if str(local_dir) != os.path.realpath(local_dir):
        raise HarnessInvalidatedError(f"--local-dir {shown} is not its own resolved path; it passes through a symlink")
    if local_dir.name != LOCAL_DIR_NAME:
        raise HarnessInvalidatedError(f"--local-dir {shown} must end in {LOCAL_DIR_NAME!r}")
    existing_ancestor = next(parent for parent in local_dir.parents if parent.exists())
    # A path inside the directory, since a directory-only ignore pattern cannot match one that is absent.
    try:
        ignored = subprocess.run(
            ["git", "-C", str(existing_ancestor), "check-ignore", "-q", "--", str(local_dir / "ignore-probe")],
            capture_output=True, env=isolated_git_environment(), check=False,
            timeout=review_runner._ENVIRONMENT_READ_TIMEOUT_S,
        ).returncode == 0
    except subprocess.TimeoutExpired:
        ignored = False
    if not ignored:
        raise HarnessInvalidatedError(f"--local-dir {shown} is not ignored by the git work tree that holds it")


def _interrupt_hint(args: argparse.Namespace) -> str:
    campaign_id = getattr(args, "campaign_id", None)
    timeout_s = getattr(args, "timeout_s", review_runner.REVIEWER_TIMEOUT_S)
    resume = f"resume with --campaign-id {campaign_id}" if campaign_id else "rerun the command"
    return (
        f"{args.subcommand}: interrupted; {resume}. After a hard kill, wait at least --timeout-s ({timeout_s} s) "
        "first, since a surviving claude child can still be writing. Resume with the same TMPDIR "
        f"({os.environ.get('TMPDIR', '/tmp')!r}), since the sweep matches logged fixtures only directly under the "
        "system temp dir."
    )


def _safe_streams() -> contextlib.ExitStack:
    stack = contextlib.ExitStack()
    stack.enter_context(contextlib.redirect_stdout(SafeStream(sys.stdout)))
    stack.enter_context(contextlib.redirect_stderr(SafeStream(sys.stderr)))
    return stack


def main(argv: list[str] | None = None) -> int:
    """Every line the runner writes to stdout or stderr passes through `SafeStream`, so no refusal, halt or report
    can put an escape sequence from a child, an agent or a config path on the terminal. Arguments parse outside the
    wrapper: argparse writes its own help, usage and colour markup, which must reach the terminal as it is."""
    with _safe_streams():
        refusal = _check_runtime()
    if refusal is not None:
        return refusal
    try:
        args = build_parser().parse_args(argv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 2
    with _safe_streams():
        return _run_main(args)


def _check_runtime() -> int | None:
    """Exit code 2 when Python or git is below its floor, else None."""
    if sys.version_info < MIN_PYTHON:
        print(f"Python {'.'.join(map(str, MIN_PYTHON))} or newer is required", file=sys.stderr)
        return 2
    try:
        _refuse_old_git()
    except HarnessInvalidatedError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return None


def _run_main(args: argparse.Namespace) -> int:
    os.umask(0o077)
    local_dir = Path(args.local_dir) if args.local_dir else DEFAULT_LOCAL_DIR
    # A `claude -p` child leads its own session, so a hangup never reaches it. `nohup` starts the process with
    # SIGHUP ignored, and that must stand, or a detached campaign would abort at logout.
    if signal.getsignal(signal.SIGHUP) is not signal.SIG_IGN:
        signal.signal(signal.SIGHUP, _raise_signal_interrupt)
    signal.signal(signal.SIGTERM, _raise_signal_interrupt)
    try:
        check_local_dir(local_dir)
        import run_review_bench

        run_review_bench._restrict_local_dir_to_owner(local_dir)
        local_dir.mkdir(parents=True, exist_ok=True)
        return args.func(args, local_dir)
    except (HarnessInvalidatedError, tasks.TaskSourceError, tasks.TaskFileError, InvalidIdentifierError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except RunStoreLocked as exc:
        print(f"{exc} -- another run on this store is still in progress", file=sys.stderr)
        return 1
    except KeyboardInterrupt as exc:
        msmr.abort_launches()
        print(_interrupt_hint(args), file=sys.stderr)
        return 128 + getattr(exc, "signum", signal.SIGINT)


if __name__ == "__main__":
    sys.exit(main())
