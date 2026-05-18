"""Skill trigger-fidelity harness for claude-config.

Runs `claude -p` against per-skill trigger-cases.json files and reports
how reliably each skill auto-triggers (or stays silent) for its declared
TRIGGER / DO NOT TRIGGER conditions.

LOCAL USE ONLY — never run in CI. Uses the session's Claude subscription
auth (no ANTHROPIC_API_KEY required; no per-token charge on Max plan).

Sampling and subprocess structure adapted from Anthropic's scripts/run_eval.py in
anthropics/skills; detection rewritten for Claude Code Skill-tool dispatch.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent
SKILLS_DIR = REPO_ROOT / "claude" / ".claude" / "skills"
PLUGINS_DIR = REPO_ROOT / "plugins"

SAMPLE_TIMEOUT_S = 90
DEFAULT_SAMPLES = 10
DEFAULT_WORKERS = 4
DEFAULT_MODEL = "claude-sonnet-4-6"

# Measurement method declared per case file. `runtime` skills are measured by
# this harness, which spawns `claude -p` and watches for the Skill tool to fire.
# `description-fidelity` skills are measured by a separate runner — see
# evals/README.md. A case file must declare one of these.
RUNTIME_METHOD = "runtime"
VALID_METHODS = frozenset((RUNTIME_METHOD, "description-fidelity"))

# Skill-tool detection: Claude Code auto-triggers a skill by calling the Skill tool.
# Read is NOT included — its input is a file path, not a skill name.
TRIGGER_TOOL_NAMES = frozenset(("Skill",))


def find_skill_dir(skill_name: str) -> Path | None:
    candidate = SKILLS_DIR / skill_name
    if candidate.is_dir():
        return candidate
    if PLUGINS_DIR.exists():
        for plugin_dir in sorted(PLUGINS_DIR.iterdir()):
            c = plugin_dir / "skills" / skill_name
            if c.is_dir():
                return c
    return None


def discover_case_files() -> list[Path]:
    results = []
    for p in SKILLS_DIR.glob("*/evals/trigger-cases.json"):
        results.append(p)
    if PLUGINS_DIR.exists():
        for p in PLUGINS_DIR.glob("*/skills/*/evals/trigger-cases.json"):
            results.append(p)
    return sorted(results)


def load_case_file(path: Path) -> dict:
    with open(path) as f:
        data = json.load(f)
    if "skill_name" not in data or "cases" not in data:
        raise ValueError(f"Invalid trigger-cases.json at {path}: missing skill_name or cases")
    method = data.get("method")
    if method not in VALID_METHODS:
        raise ValueError(
            f"Invalid trigger-cases.json at {path}: 'method' must be one of "
            f"{sorted(VALID_METHODS)}, got {method!r}"
        )
    return data


def partition_case_files(case_files: list[Path]) -> tuple[list[Path], list[tuple[str, str]]]:
    """Split case files into runtime-measurable and skipped (non-runtime).

    Returns (runtime_files, skipped); skipped holds (skill_name, method) pairs
    for skills this harness cannot measure. Raises via load_case_file() on any
    file with a missing or invalid method.
    """
    runtime_files: list[Path] = []
    skipped: list[tuple[str, str]] = []
    for case_file in case_files:
        data = load_case_file(case_file)
        if data["method"] == RUNTIME_METHOD:
            runtime_files.append(case_file)
        else:
            skipped.append((data["skill_name"], data["method"]))
    return runtime_files, skipped


def format_skip_notice(skipped: list[tuple[str, str]]) -> str:
    """One-line report of skills excluded because they are not runtime-measurable."""
    names = ", ".join(sorted(name for name, _ in skipped))
    return (
        f"Skipped {len(skipped)} skill(s) not measured by the runtime harness: "
        f"{names}. See evals/README.md for the measurement method each skill uses."
    )


def seed_temp_project_git(project_dir: Path) -> None:
    """Copy the committed fixture skeleton into project_dir and set up git state.

    Static content (README, calculator.py, tests/) comes from
    evals/fixtures/temp-project/. The staged-but-uncommitted diff comes from
    evals/fixtures/temp-project.staged.patch. Git identity is pinned via -c
    flags so this does not depend on machine git config.
    """
    fixture_dir = EVALS_DIR / "fixtures" / "temp-project"
    staged_patch = EVALS_DIR / "fixtures" / "temp-project.staged.patch"

    shutil.copytree(fixture_dir, project_dir, dirs_exist_ok=True)

    git = ["git", "-c", "user.email=eval@example.com", "-c", "user.name=Eval Harness"]
    subprocess.run([*git, "init", "-q"], cwd=project_dir, check=True)
    subprocess.run([*git, "add", "-A"], cwd=project_dir, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "initial"], cwd=project_dir, check=True)
    subprocess.run(["git", "apply", str(staged_patch)], cwd=project_dir, check=True)
    subprocess.run(["git", "add", "-A"], cwd=project_dir, check=True)


def build_temp_project(skills_dir: Path, plugins_dir: Path) -> Path:
    """Create a throwaway project with real working-tree skills symlinked.

    Uses approach A from the plan: real skills in real mutual competition.
    The temp project has no workflow hooks (minimal settings.json).
    Skills with disable-model-invocation: true stay present as competition
    but are not scored.
    """
    tmp = Path(tempfile.mkdtemp(prefix="claude-eval-"))
    dot_claude = tmp / ".claude"
    dot_claude.mkdir()
    (dot_claude / "skills").symlink_to(skills_dir)
    if plugins_dir.exists():
        plugin_skills = dot_claude / "plugin_skills"
        plugin_skills.mkdir()
        for plugin_dir in sorted(plugins_dir.iterdir()):
            ps = plugin_dir / "skills"
            if ps.is_dir():
                (plugin_skills / plugin_dir.name).symlink_to(ps)
    (dot_claude / "settings.json").write_text(json.dumps({"hooks": {}}))
    seed_temp_project_git(tmp)
    return tmp


def detect_trigger_in_lines(
    lines: Iterable[str | bytes], skill_name: str, also_not: list[str]
) -> tuple[str | None, list[str]]:
    """Parse stream-json lines and return which skill fired (if any).

    Returns (fired_skill_name_or_None, list_of_also_not_skills_that_fired).
    Scans all lines without early termination so later also_not blocks are observed.
    Separated from the subprocess layer so the unit test can feed fixture
    files without spawning a subprocess.
    """
    fired_skill: str | None = None
    also_fired: list[str] = []
    current_tool_name: str | None = None
    current_partial: str = ""

    for raw_line in lines:
        line = raw_line.strip().decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        evt = obj.get("event")
        if not isinstance(evt, dict):
            continue

        evt_type = evt.get("type")

        if evt_type == "content_block_start":
            cb = evt.get("content_block", {})
            if cb.get("type") == "tool_use":
                tool = cb.get("name", "")
                if tool in TRIGGER_TOOL_NAMES:
                    current_tool_name = tool
                    current_partial = ""
                else:
                    current_tool_name = None
                    current_partial = ""

        elif evt_type == "content_block_delta" and current_tool_name:
            delta = evt.get("delta", {})
            if delta.get("type") == "input_json_delta":
                current_partial += delta.get("partial_json", "")

        elif evt_type == "content_block_stop" and current_tool_name:
            try:
                payload = json.loads(current_partial)
            except json.JSONDecodeError:
                payload = None
            invoked = payload.get("skill") if isinstance(payload, dict) else None
            if invoked == skill_name:
                fired_skill = skill_name
            for excluded in also_not:
                if invoked == excluded:
                    also_fired.append(excluded)
            current_tool_name = None
            current_partial = ""

    return fired_skill, also_fired


def detect_trigger_in_stream(
    proc: subprocess.Popen, skill_name: str, also_not: list[str]
) -> tuple[str | None, list[str]]:
    """Read stream-json from proc and return which skill fired.

    Early-terminates the subprocess once the target skill fires and there are no
    also_not guards remaining to observe. When also_not is non-empty, reads to
    completion/timeout so every block is seen.
    """
    buf = b""
    all_lines: list[bytes] = []
    deadline = time.monotonic() + SAMPLE_TIMEOUT_S

    while True:
        if time.monotonic() > deadline:
            break
        remaining = max(0.1, deadline - time.monotonic())
        rlist, _, _ = select.select([proc.stdout], [], [], remaining)
        if not rlist:
            continue
        chunk = proc.stdout.read(4096)
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            all_lines.append(line)
        fired, _ = detect_trigger_in_lines(all_lines, skill_name, also_not)
        if fired is not None and not also_not:
            break

    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass

    return detect_trigger_in_lines(all_lines, skill_name, also_not)


def run_single_sample(args: tuple) -> tuple[str | None, list[str]]:
    """Run one claude -p sample. Called from worker processes."""
    query, skill_name, also_not, tmp_project, model = args

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = subprocess.Popen(
        [
            "claude", "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
        ],
        cwd=str(tmp_project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    return detect_trigger_in_stream(proc, skill_name, also_not)


def run_case(
    case: dict,
    skill_name: str,
    tmp_project: Path,
    model: str,
    samples: int,
    workers: int,
    verbose: bool,
) -> dict:
    query = case["query"]
    should_trigger = case["should_trigger"]
    also_not = case.get("also_not_triggered", [])
    case_id = case.get("id", query[:40])

    fired_count = 0
    also_fired_totals: dict[str, int] = {}

    sample_args = [(query, skill_name, also_not, tmp_project, model)] * samples

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_single_sample, a) for a in sample_args]
        for fut in as_completed(futures):
            fired_skill, also_fired = fut.result()
            if fired_skill == skill_name:
                fired_count += 1
            for s in also_fired:
                also_fired_totals[s] = also_fired_totals.get(s, 0) + 1

    trigger_rate = fired_count / samples
    triggered_label = "triggers" if should_trigger else "does-not-trig"

    threshold_pass = (trigger_rate >= 0.5) if should_trigger else (trigger_rate < 0.5)
    also_not_violations = {k: v for k, v in also_fired_totals.items() if v > 0}
    passed = threshold_pass and not also_not_violations

    if verbose:
        status = "PASS" if passed else "FAIL"
        violation_note = ""
        if also_not_violations:
            parts = [f"{k} fired {v}/{samples}" for k, v in also_not_violations.items()]
            violation_note = f"  ({', '.join(parts)})"
        print(f"  {case_id:<40} {triggered_label:<14} {fired_count}/{samples}   {status}{violation_note}", flush=True)

    return {
        "id": case_id,
        "should_trigger": should_trigger,
        "trigger_rate": trigger_rate,
        "fired_count": fired_count,
        "samples": samples,
        "passed": passed,
        "also_not_violations": also_not_violations,
    }


def run_skill(
    case_file: Path,
    tmp_project: Path,
    model: str,
    samples: int,
    workers: int,
    verbose: bool,
) -> dict:
    data = load_case_file(case_file)
    skill_name = data["skill_name"]
    cases = data["cases"]

    if verbose:
        print(f"\n{skill_name}", flush=True)

    case_results = []
    for case in cases:
        result = run_case(case, skill_name, tmp_project, model, samples, workers, verbose)
        case_results.append(result)

    passed = sum(1 for r in case_results if r["passed"])
    return {
        "skill_name": skill_name,
        "cases": case_results,
        "passed": passed,
        "total": len(case_results),
    }


def print_report(skill_results: list[dict], model: str, samples: int, verbose: bool) -> None:
    today = date.today().isoformat()
    print(f"\nSkill trigger-fidelity   model={model}  K={samples}   {today}\n")

    if not verbose:
        for sr in skill_results:
            print(f"{sr['skill_name']:<50} {sr['passed']}/{sr['total']}")
            for cr in sr["cases"]:
                triggered_label = "triggers" if cr["should_trigger"] else "does-not-trig"
                status = "PASS" if cr["passed"] else "FAIL"
                violation_note = ""
                if cr["also_not_violations"]:
                    parts = [f"{k} fired {v}/{cr['samples']}" for k, v in cr["also_not_violations"].items()]
                    violation_note = f"  ({', '.join(parts)})"
                print(f"  {cr['id']:<40} {triggered_label:<14} {cr['fired_count']}/{cr['samples']}   {status}{violation_note}")

    total_cases = sum(sr["total"] for sr in skill_results)
    total_pass = sum(sr["passed"] for sr in skill_results)
    fail_count = total_cases - total_pass
    summary_parts = [f"{len(skill_results)} skills, {total_cases} cases | pass {total_pass}/{total_cases}"]
    if fail_count:
        summary_parts.append(f"review the {fail_count} FAIL cases above")
    print(f"\nsummary: {' | '.join(summary_parts)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Skill trigger-fidelity harness (local only, never CI)")
    parser.add_argument(
        "--skill", action="append", dest="skills", metavar="NAME",
        help="Skill name to test (repeatable; default: all with trigger-cases.json)",
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES, metavar="K",
        help=f"Samples per case (default: {DEFAULT_SAMPLES})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, choices=["claude-sonnet-4-6", "claude-opus-4-7"],
        help=f"Model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="Write full results JSON to PATH")
    parser.add_argument("--verbose", action="store_true", help="Print case results as they complete")
    args = parser.parse_args()

    if args.skills:
        case_files = []
        for skill_name in args.skills:
            skill_dir = find_skill_dir(skill_name)
            if skill_dir is None:
                print(f"ERROR: skill '{skill_name}' not found", file=sys.stderr)
                return 1
            cf = skill_dir / "evals" / "trigger-cases.json"
            if not cf.exists():
                print(f"ERROR: no trigger-cases.json for skill '{skill_name}' at {cf}", file=sys.stderr)
                return 1
            case_files.append(cf)
    else:
        case_files = discover_case_files()
        if not case_files:
            print("No trigger-cases.json files found. Run --skill <name> or author case files first.", file=sys.stderr)
            return 1

    runtime_files, skipped = partition_case_files(case_files)
    if skipped:
        print(format_skip_notice(skipped))
    if not runtime_files:
        print("No runtime-measurable skills to run.")
        return 0

    tmp_project = build_temp_project(SKILLS_DIR, PLUGINS_DIR)
    try:
        skill_results = []
        for case_file in runtime_files:
            sr = run_skill(case_file, tmp_project, args.model, args.samples, args.workers, args.verbose)
            skill_results.append(sr)

        print_report(skill_results, args.model, args.samples, args.verbose)

        if args.json_out:
            out = {
                "model": args.model,
                "samples": args.samples,
                "date": date.today().isoformat(),
                "skills": skill_results,
            }
            Path(args.json_out).write_text(json.dumps(out, indent=2))
            print(f"Full results written to {args.json_out}")

        return 0  # always 0 — measurement, not a gate

    finally:
        shutil.rmtree(tmp_project, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
