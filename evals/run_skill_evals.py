"""Skill eval harness for claude-config.

Measures each skill's *-cases.json against its declared TRIGGER / DO NOT
TRIGGER conditions (or, for disposition-fidelity, its declared disposition
rule), using one of four methods declared per case file:

- runtime: spawn `claude -p` and watch for the Skill tool to fire. Measures
  real auto-dispatch; faithful only when the skill triggers headlessly.
- description-fidelity: ask `claude -p`, in a plain classification prompt,
  which skill a query should match given the full skill listing. Measures
  whether the skill's description discriminates the query — not runtime
  dispatch. Used for advisory skills the runtime substrate cannot reach.
- behavioral-dispatch: spawn `claude -p` and watch for the Task tool to fire.
  Measures whether the model actually delegates to a subagent when given a full
  task scenario. Used for skills like subagent-delegation whose effect is the
  parent's tool choice, not a Skill invocation.
- disposition-fidelity: ask `claude -p` to review a fixed scenario twice —
  once with no guidance (baseline) and once with the skill's governing rule
  text extracted live from its SKILL.md (treatment) — then judge each
  review's disposition against the case's rubric. Measures whether the rule
  text actually drives the correct disposition, not just whether it fires.
  See evals/README.md's disposition-fidelity section for the two-layer model.

LOCAL USE ONLY — never run in CI. Uses the session's Claude subscription
auth (no ANTHROPIC_API_KEY required; no per-token charge on Max plan).

Sampling and subprocess structure adapted from Anthropic's scripts/run_eval.py in
anthropics/skills; detection rewritten for Claude Code Skill-tool dispatch.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import select
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVALS_DIR = Path(__file__).resolve().parent
SKILLS_DIR = REPO_ROOT / "claude" / ".claude" / "skills"
PLUGINS_DIR = REPO_ROOT / "plugins"

sys.path.insert(0, str(REPO_ROOT / "claude" / ".claude" / "scripts"))
from _config_dir import config_dir  # noqa: E402

# Priming prompt for warm dispatch mode. The file instructs the model to
# actually read dispatch-project files via the Read tool, generating real
# tool-call history that physically fills the session context window.
DISPATCH_PRIMING_PROMPT_FILE = EVALS_DIR / "fixtures" / "dispatch-priming-prompt.md"

SAMPLE_TIMEOUT_S = 90
DEFAULT_SAMPLES = 10
DEFAULT_WORKERS = 4
DEFAULT_MODEL = "claude-sonnet-4-6"

# Measurement method declared per case file. `runtime` spawns `claude -p` and
# watches for the Skill tool to fire; `description-fidelity` asks `claude -p` to
# classify which skill a query should match; `behavioral-dispatch` spawns
# `claude -p` and watches for the Task tool to fire; `disposition-fidelity`
# asks `claude -p` to review a scenario with and without the skill's governing
# rule text and judges each review's disposition. See evals/README.md. A
# case file must declare exactly one of these.
RUNTIME_METHOD = "runtime"
DESCRIPTION_FIDELITY_METHOD = "description-fidelity"
BEHAVIORAL_DISPATCH_METHOD = "behavioral-dispatch"
DISPOSITION_FIDELITY_METHOD = "disposition-fidelity"
VALID_METHODS = frozenset((
    RUNTIME_METHOD, DESCRIPTION_FIDELITY_METHOD, BEHAVIORAL_DISPATCH_METHOD, DISPOSITION_FIDELITY_METHOD,
))

# Skill-tool detection: Claude Code auto-triggers a skill by calling the Skill tool.
# Read is NOT included — its input is a file path, not a skill name.
TRIGGER_TOOL_NAMES = frozenset(("Skill",))

# Dispatch-tool detection: current Claude Code (>=2.1.191) emits "Agent" for
# subagent dispatch; earlier versions emitted "Task". Both are matched so the
# harness stays correct across a version boundary. Confirmed by capturing a
# live claude -p stream: the model calls Agent even when instructed to use Task.
DISPATCH_TOOL_NAMES = frozenset(("Agent", "Task"))


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
    for p in SKILLS_DIR.glob("*/evals/*-cases.json"):
        results.append(p)
    if PLUGINS_DIR.exists():
        for p in PLUGINS_DIR.glob("*/skills/*/evals/*-cases.json"):
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


def partition_case_files(case_files: list[Path]) -> dict[str, list[Path]]:
    """Partition case files by measurement method.

    Returns dict[method_str, list[Path]]. Raises via load_case_file() on any
    file with a missing or invalid method. Forward-compatible: a new method
    constant added to VALID_METHODS is automatically tracked here.
    """
    by_method: dict[str, list[Path]] = {m: [] for m in VALID_METHODS}
    for case_file in case_files:
        method = load_case_file(case_file)["method"]
        by_method.setdefault(method, []).append(case_file)
    return by_method


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


# --- description-fidelity measurement -------------------------------------
#
# This path does not exercise skill auto-dispatch. It asks `claude -p` a plain
# question — "which skill should handle this query?" — given the skill listing
# as prompt data. Plain question-answering is unaffected by the headless
# auto-trigger limitation (anthropics/claude-code#34648), so it is an honest
# fallback for advisory skills the `runtime` substrate cannot measure.


def parse_skill_frontmatter(skill_md_path: Path) -> tuple[str, str]:
    """Extract (name, description) from a SKILL.md YAML frontmatter block.

    Minimal stdlib parser — handles inline scalars and `>`/`|` block scalars.
    Avoids a PyYAML dependency so this module imports cleanly under CI, which
    installs only pytest and ruff. Falls back to the directory name when the
    `name` key is absent or there is no frontmatter.
    """
    text = skill_md_path.read_text()
    fallback_name = skill_md_path.parent.name
    if not text.startswith("---"):
        return fallback_name, ""
    try:
        closing = text.index("\n---", 3)
    except ValueError:
        return fallback_name, ""
    lines = text[3:closing].splitlines()

    name = fallback_name
    description = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("name:"):
            value = line.split(":", 1)[1].strip()
            if value:
                name = value
            i += 1
        elif line.startswith("description:"):
            value = line.split(":", 1)[1].strip()
            if value and value not in (">", "|", ">-", "|-", ">+", "|+"):
                description = value
                i += 1
            else:
                # Block scalar: gather blank or indented continuation lines,
                # stopping at the next top-level key.
                block: list[str] = []
                i += 1
                while i < len(lines) and (not lines[i].strip() or lines[i][:1] in (" ", "\t")):
                    block.append(lines[i].strip())
                    i += 1
                description = " ".join(part for part in block if part)
        else:
            i += 1
    return name, description


def assemble_skill_listing() -> tuple[str, frozenset[str]]:
    """Build the skill listing shown to the classifier and the set of valid names.

    Reads name + description frontmatter from every SKILL.md under SKILLS_DIR
    and the plugin skill dirs — the same paths discover_case_files() globs — so
    adjacent skills compete in the classification prompt exactly as they do in
    a live session's skill listing.
    """
    skill_md_paths = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if PLUGINS_DIR.exists():
        skill_md_paths += sorted(PLUGINS_DIR.glob("*/skills/*/SKILL.md"))

    entries: list[str] = []
    names: list[str] = []
    for path in skill_md_paths:
        name, description = parse_skill_frontmatter(path)
        names.append(name)
        entries.append(f"- {name}: {description}")
    return "\n".join(entries), frozenset(names)


def build_isolated_project() -> Path:
    """Create a throwaway project with no skills, for description-fidelity runs.

    The classification prompt carries the skill listing as data and asks a
    plain question; `claude -p` must answer it, not auto-dispatch a skill. An
    empty project (no .claude/skills/) keeps project-scope skills and hooks out
    of the way. settings.json disables hooks.
    """
    tmp = Path(tempfile.mkdtemp(prefix="claude-eval-df-"))
    dot_claude = tmp / ".claude"
    dot_claude.mkdir()
    (dot_claude / "settings.json").write_text(json.dumps({"hooks": {}}))
    return tmp


def build_dispatch_project(skills_dir: Path, plugins_dir: Path) -> Path:
    """Create a throwaway project for behavioral-dispatch runs.

    Like build_temp_project — real skills symlinked so the subagent-delegation
    skill is present and shaping the model — but seeded with the dispatch
    fixture project (evals/fixtures/dispatch-project/) instead of the
    calculator project. The dispatch fixture has a real multi-file import graph
    so "find every file that imports X" scenarios are genuine sweeps.
    """
    tmp = Path(tempfile.mkdtemp(prefix="claude-eval-bd-"))
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

    dispatch_fixture = EVALS_DIR / "fixtures" / "dispatch-project"
    shutil.copytree(dispatch_fixture, tmp, dirs_exist_ok=True)

    git = ["git", "-c", "user.email=eval@example.com", "-c", "user.name=Eval Harness"]
    subprocess.run([*git, "init", "-q"], cwd=tmp, check=True)
    subprocess.run([*git, "add", "-A"], cwd=tmp, check=True)
    subprocess.run([*git, "commit", "-q", "-m", "initial"], cwd=tmp, check=True)

    return tmp


def build_classification_prompt(skill_listing: str, query: str) -> str:
    """Construct the single-query classification prompt for description-fidelity.

    The answer is constrained to one line — one skill name or the literal
    `none` — so parse_classification_answer() is deterministic.
    """
    return (
        "You are classifying which Claude Code skill, if any, should handle a "
        "user request.\n\n"
        "Below is the full list of available skills with their descriptions. "
        "Each description states when the skill should and should not be "
        "used.\n\n"
        f"<skills>\n{skill_listing}\n</skills>\n\n"
        "A user has made this request:\n\n"
        f"<request>\n{query}\n</request>\n\n"
        "Which single skill should handle this request? Reply with exactly one "
        "line containing only the skill name, or the literal word none if no "
        "skill applies. Do not explain."
    )


def parse_classification_answer(raw_output: str, valid_skill_names: frozenset[str]) -> str | None:
    """Parse a classifier reply into a skill name, or None for 'none'/unparseable.

    Accepts the constrained one-line answer (a bare skill name or `none`) and,
    as a best-effort fallback, a prose-wrapped name. Returning the *named*
    skill — not a boolean — lets description-fidelity score also_not_triggered
    violations the same way runtime mode does.
    """
    text = raw_output.strip()
    if not text:
        return None

    last_line = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line.strip().strip("\"'`. ").lower()
            break
    if last_line == "none":
        return None
    if last_line in valid_skill_names:
        return last_line

    # Best-effort fallback for a prose-wrapped answer. Longest name first so a
    # name that is a substring of another cannot mask the longer match.
    for name in sorted(valid_skill_names, key=len, reverse=True):
        if re.search(rf"\b{re.escape(name)}\b", text):
            return name
    return None


def score_classification(
    named_skill: str | None, skill_name: str, also_not: list[str]
) -> tuple[str | None, list[str]]:
    """Map a classifier's named skill to the (fired, also_fired) scoring shape.

    Shared by run_description_fidelity_sample() and its unit tests. A named
    skill listed in also_not is an also_not_triggered violation — scored
    exactly as a runtime misfire is.
    """
    fired = skill_name if named_skill == skill_name else None
    also_fired = [excluded for excluded in also_not if excluded == named_skill]
    return fired, also_fired


def run_description_fidelity_sample(args: tuple) -> tuple[str | None, list[str]]:
    """Run one classification sample. Called from worker processes.

    Returns the same (fired_skill_or_None, also_fired) shape as
    run_single_sample() so run_case() scores both methods identically.
    """
    query, skill_name, also_not, isolated_project, skill_listing, valid_names, model = args

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    prompt = build_classification_prompt(skill_listing, query)

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", model],
            cwd=str(isolated_project),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SAMPLE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None, []

    named = parse_classification_answer(proc.stdout, valid_names)
    return score_classification(named, skill_name, also_not)


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


# --- behavioral-dispatch measurement ------------------------------------------
#
# Watches the Agent and Task tools, not the Skill tool. Claude Code >=2.1.191
# dispatches subagents via the Agent tool; earlier versions used Task. Both are
# matched. "Fired" means any Agent or Task call occurred; there is no payload
# field to match, and behavioral-dispatch uses no also_not.


def detect_dispatch_in_lines(lines: Iterable[str | bytes]) -> bool:
    """Parse stream-json lines and return True if any Agent or Task tool_use appeared.

    Mirrors detect_trigger_in_lines but simpler: no also_not, no payload
    parsing. Separated from the subprocess layer so the unit test can feed
    fixture files without spawning a subprocess.
    """
    for raw_line in lines:
        line = (
            raw_line.strip().decode("utf-8", errors="replace")
            if isinstance(raw_line, bytes)
            else raw_line.strip()
        )
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        evt = obj.get("event")
        if not isinstance(evt, dict):
            continue

        if evt.get("type") != "content_block_start":
            continue
        block = evt.get("content_block", {})
        if block.get("type") == "tool_use" and block.get("name") in DISPATCH_TOOL_NAMES:
            return True

    return False


def detect_dispatch_in_stream(proc: subprocess.Popen) -> bool:
    """Read stream-json from proc stdout; return True on first Agent or Task tool_use.

    Early-terminates the subprocess on first dispatch tool call — no also_not to
    wait for. Mirrors detect_trigger_in_stream.
    """
    buf = b""
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
            if detect_dispatch_in_lines([line]):
                try:
                    proc.kill()
                    proc.wait(timeout=2)
                except Exception:
                    pass
                return True

    try:
        proc.kill()
        proc.wait(timeout=2)
    except Exception:
        pass

    return False


def _build_dispatch_command(
    query: str, handoff: str, model: str, *, warm: bool = False, session_id: str | None = None
) -> list[str]:
    """Build the claude -p command list for one behavioral-dispatch sample.

    When warm=True and session_id is provided, resumes the primed session via
    --fork-session (each sample gets its own forked UUID; the primed base is
    immutable). When warm=False (default), injects context via --append-system-prompt.
    """
    if warm and session_id is not None:
        return [
            "claude", "-p", query,
            "--output-format", "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--model", model,
            "--resume", session_id,
            "--fork-session",
        ]
    return [
        "claude", "-p", query,
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--model", model,
        "--append-system-prompt", handoff,
    ]


def compute_session_store_dir(project_dir: Path) -> Path:
    """Return the <config_dir>/projects/<path-hash> directory for a given project path.

    Claude Code stores sessions at <config_dir>/projects/<hash>/ where <hash> is
    the project's absolute path with every "/" replaced by "-", and <config_dir>
    is CLAUDE_CONFIG_DIR if set, else ~/.claude. Sessions live externally from
    the project directory; shutil.rmtree(project_dir) does not clean them.
    Verified by inspecting ~/.claude/projects/ before and after a dispatch run:
    tempdir-based dispatch projects accumulate session dirs at this location
    that survive the project cleanup.
    """
    hashed_name = str(project_dir).replace("/", "-")
    return config_dir() / "projects" / hashed_name


def prime_dispatch_session(dispatch_project: Path, priming_prompt: str, model: str) -> str:
    """Run a single priming invocation against dispatch_project and return its session ID.

    Assigns a UUID via --session-id so the session can be resumed by all parallel
    samples via --fork-session. The priming invocation does not use stream-json
    output — its output is discarded; the side effect (a persisted session with
    real Read-tool history) is all that matters.

    Does NOT use --no-session-persistence: the session must be saved so samples
    can --resume it. Spike 1 confirmed that --no-session-persistence prevents
    resume with "No conversation found".

    Timeout is 3× SAMPLE_TIMEOUT_S (270 s) because priming involves reading
    multiple files and writing analysis — substantially more work than a single
    sample turn.
    """
    session_id = str(uuid.uuid4())
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    try:
        subprocess.run(
            ["claude", "-p", priming_prompt, "--session-id", session_id, "--model", model],
            cwd=str(dispatch_project),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=SAMPLE_TIMEOUT_S * 3,
        )
    except subprocess.TimeoutExpired:
        print(
            "Warning: priming invocation timed out; samples will attempt --resume anyway",
            file=sys.stderr,
        )
    return session_id


def run_dispatch_sample(args: tuple) -> tuple[str | None, list[str]]:
    """Run one claude -p sample for behavioral-dispatch. Called from worker processes.

    Returns (skill_name if the model dispatched a subagent else None, []) —
    the same (fired, also_fired) shape as run_single_sample so run_case()
    scores both methods identically.

    args is a positional 7-tuple:
        (query, skill_name, dispatch_project, handoff, model, warm, primed_session_id).
    Both this unpack and the construction in run_case() must be updated together.
    """
    query, skill_name, dispatch_project, handoff, model, warm, primed_session_id = args

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    proc = subprocess.Popen(
        _build_dispatch_command(query, handoff, model, warm=warm, session_id=primed_session_id),
        cwd=str(dispatch_project),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    dispatched = detect_dispatch_in_stream(proc)
    proc.stdout.close()
    return (skill_name if dispatched else None, [])


# --- disposition-fidelity measurement -----------------------------------------
#
# Neither trigger detection nor classification: this measures whether a skill's
# governing rule text actually drives the correct review *disposition*. Each
# sample runs two `claude -p` reviews of the same scenario — baseline (a
# neutral frame only, no rule) and treatment (the neutral frame plus the rule
# text extracted live from the current SKILL.md) — then a third `claude -p`
# call judges each review's disposition against the case's rubric. See
# evals/README.md's disposition-fidelity section for the full design rationale
# (neutral-frame isolation, the routine gate, the drift alarm).

# Gate thresholds for score_disposition_results(). Heuristic acceptance
# thresholds for a local-only dev tool (not a production SLA or vendor-
# specified value — CLAUDE.md's numeric-literal citation requirement is
# scoped to network/timeout/retry contexts, which these aren't).
DISPOSITION_MIN_EFFECTIVE_SAMPLES = 6  # below this, exclusions hollow the denominator too far to trust the rate
# 0.8 (not 0.5): a correct rule blocks near-always, so this catches a
# ~0.95 -> ~0.55 efficacy regression that a lower bar would let pass forever.
DISPOSITION_PASS_THRESHOLD = 0.8
# Non-gating: baseline creeping to this rate means the no-guidance control is
# starting to block on its own — re-author the fixture.
DISPOSITION_DRIFT_ALARM_THRESHOLD = 0.3

# Neutral per-skill task frame. Deliberately says nothing about the disposition
# rule under test — it is the no-guidance control half of the baseline/
# treatment pair. Keyed by skill_name because plan-review and code-review
# review different artifacts (a plan vs. a diff).
DISPOSITION_FRAMES: dict[str, str] = {
    "plan-review": (
        "You are reviewing an implementation plan before it proceeds to execution. "
        "Read the plan below and decide your review disposition: Approve, Approve "
        "with changes, or Request changes. State your verdict as the last line of "
        "your reply, in the form 'Verdict: <your verdict>'."
    ),
    "code-review": (
        "You are reviewing a code diff before it merges. Read the diff below and "
        "decide your review disposition for each finding: ADDRESS (fix it before "
        "merge, or raise a blocking stop-and-ask) or DEFER (note it and proceed). "
        "State your verdict as the last line of your reply, in the form "
        "'Verdict: <your verdict>'."
    ),
}


def extract_governing_rule(skill_md_path: Path, anchor_name: str) -> str:
    """Extract the text of a `<!-- DISPOSITION_RULE:<anchor_name> ... -->` anchor block.

    Returns the text strictly between the start and end anchor comments, with
    the anchor comment lines themselves excluded. Keyed specifically on the
    `DISPOSITION_RULE:` prefix so this never matches the pre-existing
    `HOOK_TEST_FIXTURE` comments already present in these same SKILL.md files.

    Raises ValueError — never returns an empty string or silently no-ops — when
    the anchor is missing, misspelled, duplicated, or only one of start/end is
    present. A silent no-op here would make treatment == baseline and destroy
    the eval's signal without any indication why; a silently-picked duplicate
    would extract text from the wrong start/end pair without any indication
    the SKILL.md contains two blocks under the same name.
    """
    text = skill_md_path.read_text()
    start_marker = f"<!-- DISPOSITION_RULE:{anchor_name} start -->"
    end_marker = f"<!-- DISPOSITION_RULE:{anchor_name} end -->"
    start_count = text.count(start_marker)
    end_count = text.count(end_marker)
    if start_count > 1 or end_count > 1:
        raise ValueError(
            f"DISPOSITION_RULE anchor {anchor_name!r} in {skill_md_path}: "
            f"appears more than once (start x{start_count}, end x{end_count}) — "
            "duplicate anchor names are not supported, rename one of them"
        )
    start_idx = text.find(start_marker)
    end_idx = text.find(end_marker)
    if start_idx == -1 or end_idx == -1:
        raise ValueError(
            f"DISPOSITION_RULE anchor {anchor_name!r} not found (or incomplete) in "
            f"{skill_md_path} — start present: {start_idx != -1}, end present: {end_idx != -1}"
        )
    if end_idx < start_idx:
        raise ValueError(
            f"DISPOSITION_RULE anchor {anchor_name!r} in {skill_md_path}: "
            "end marker appears before start marker"
        )
    return text[start_idx + len(start_marker):end_idx].strip()


def build_disposition_prompt(frame: str, scenario: str, rule_text: str | None) -> str:
    """Assemble a disposition-fidelity review prompt.

    Baseline arm passes rule_text=None (neutral frame + scenario only).
    Treatment arm passes the anchor text extracted live via
    extract_governing_rule().
    """
    parts = [frame, f"\n<scenario>\n{scenario}\n</scenario>"]
    if rule_text is not None:
        parts.append(f"\n<governing_rule>\n{rule_text}\n</governing_rule>")
    return "\n".join(parts)


def parse_disposition_answer(raw_output: str) -> str | None:
    """Parse a disposition judge's reply into "BLOCKING", "PERMISSIVE", or None.

    Mirrors parse_classification_answer's parsing style: last non-empty line,
    punctuation/quotes stripped, case-insensitive match. The strip set is
    wider than parse_classification_answer's — it also covers `!`, `,`, and
    markdown emphasis (`**BLOCKING**`), since a judge asked for a bare verdict
    word plausibly still wraps it in sentence or markdown punctuation. Falls
    back to a whole-text `\bBLOCKING\b` / `\bPERMISSIVE\b` search when the
    last line keeps the label but embeds it in a short sentence (e.g.
    "Verdict: BLOCKING") rather than being the bare word — a realistic
    non-compliance mode for a judge asked for a one-word reply. Unlike
    parse_classification_answer's fallback (which only risks masking a
    shorter name inside a longer one, resolved by longest-name-first), a
    disposition reply can contain BOTH labels via ordinary negation prose
    ("not blocking, it's clearly permissive") — picking one by any fixed
    priority would silently invert the judge's actual verdict. So the
    fallback requires *exactly one* label present; both-or-neither returns
    None. Returns None for neither/both label — the sample is excluded from
    the denominator, not folded into either arm (see run_disposition_sample())
    — so an under-stripped parser would silently manifest as extra exclusions
    rather than a visible parse failure or a mislabeled sample.
    """
    text = raw_output.strip()
    if not text:
        return None

    last_line = ""
    for line in reversed(text.splitlines()):
        if line.strip():
            last_line = line.strip().strip("\"'`.,!* ").upper()
            break
    if last_line in ("BLOCKING", "PERMISSIVE"):
        return last_line

    # Best-effort fallback for a prose-wrapped answer, searching the whole
    # text rather than last-line-only since the label can land anywhere in a
    # judge reply that ignored the one-word-only instruction. Requires
    # exactly one label present — see the both-labels note above.
    text_upper = text.upper()
    found_labels = [label for label in ("BLOCKING", "PERMISSIVE") if re.search(rf"\b{label}\b", text_upper)]
    return found_labels[0] if len(found_labels) == 1 else None


def judge_disposition(review_output: str, rubric: str, judge_model: str, cwd: Path) -> str | None:
    """Classify a review's disposition as BLOCKING or PERMISSIVE against a case rubric.

    A second `claude -p` call, mirroring run_description_fidelity_sample's
    subprocess.run shape exactly (blocking call, plain text output, no
    --output-format) since this is a plain classification question, not a
    dispatch measurement. cwd must be the isolated disposition project — the
    same "repo's real hooks/skills fire" concern that requires an isolated
    cwd for the review calls applies equally here; a judge call is still a
    `claude -p` invocation subject to whatever project settings its cwd carries.

    Returns None (excluded from the denominator, per run_disposition_sample())
    on timeout, a non-zero exit code, or empty stdout — not just on an
    unparseable label. subprocess.run does not raise on a non-zero exit unless
    check=True is passed; without the returncode check, a failed call (auth
    expiry, rate limit, CLI crash) that still writes something to stdout would
    otherwise be judged as if it were a real answer, silently corrupting the
    block-rate gate instead of being excluded.
    """
    prompt = (
        "You are classifying a review's stated disposition against a rubric.\n\n"
        f"<rubric>\n{rubric}\n</rubric>\n\n"
        f"<review>\n{review_output}\n</review>\n\n"
        "Based only on the rubric above, is the review's disposition BLOCKING or "
        "PERMISSIVE? Reply with exactly one line containing only the word "
        "BLOCKING or PERMISSIVE. Do not explain."
    )
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    try:
        proc = subprocess.run(
            ["claude", "-p", prompt, "--model", judge_model, "--no-session-persistence"],
            cwd=str(cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=SAMPLE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return None

    if proc.returncode != 0 or not proc.stdout.strip():
        return None

    return parse_disposition_answer(proc.stdout)


def run_disposition_sample(args: tuple) -> tuple[bool | None, bool | None]:
    """Run one disposition-fidelity sample: baseline + treatment review, each judged.

    Called from worker processes. args is a positional 7-tuple:
        (frame, scenario, rule_text, rubric, disposition_project, model, judge_model).
    Both this unpack and the construction in run_disposition_case() must be
    updated together.

    Returns (treatment_blocked, baseline_blocked). Either is None when its
    judge call times out, errors, or returns neither label — excluded from
    the denominator, never folded into a label (see run_disposition_case()).

    Known limitation: unlike prime_dispatch_session's warm-dispatch path,
    these calls pass --no-session-persistence (no need to --resume them), so
    they do not leak a ~/.claude/projects/<hash> session-store directory the
    way run_description_fidelity_sample's isolated_project calls still do
    (that pre-existing leak is out of scope here — see evals/README.md).
    """
    frame, scenario, rule_text, rubric, disposition_project, model, judge_model = args

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

    def _run_review(prompt: str) -> str | None:
        try:
            proc = subprocess.run(
                ["claude", "-p", prompt, "--model", model, "--no-session-persistence"],
                cwd=str(disposition_project),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=SAMPLE_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return None
        # A non-zero exit or empty stdout means the review never actually
        # completed (auth expiry, rate limit, CLI crash) — treat it the same
        # as a timeout (excluded downstream) rather than feeding a failed
        # call's leftover stdout to the judge as if it were a real review.
        if proc.returncode != 0 or not proc.stdout.strip():
            return None
        return proc.stdout

    baseline_output = _run_review(build_disposition_prompt(frame, scenario, None))
    treatment_output = _run_review(build_disposition_prompt(frame, scenario, rule_text))

    baseline_answer = (
        judge_disposition(baseline_output, rubric, judge_model, disposition_project)
        if baseline_output is not None else None
    )
    treatment_answer = (
        judge_disposition(treatment_output, rubric, judge_model, disposition_project)
        if treatment_output is not None else None
    )

    label_to_blocked = {"BLOCKING": True, "PERMISSIVE": False}
    return label_to_blocked.get(treatment_answer), label_to_blocked.get(baseline_answer)


def score_disposition_results(treatment_results: list[bool], baseline_results: list[bool]) -> dict:
    """Aggregate one case's non-excluded sample labels into rates and a verdict.

    Pure function — shared by run_disposition_case() and its unit tests, the
    same extraction pattern score_classification() uses for run_case(). Both
    input lists have already had excluded (None) samples dropped by the
    caller; this function only does the rate math, the
    DISPOSITION_MIN_EFFECTIVE_SAMPLES inconclusive floor, the
    DISPOSITION_PASS_THRESHOLD gate, and the DISPOSITION_DRIFT_ALARM_THRESHOLD
    diagnostic — none of which needs a ProcessPoolExecutor or a live claude -p
    call to test.
    """
    treatment_n = len(treatment_results)
    baseline_n = len(baseline_results)
    treatment_block_rate = (sum(treatment_results) / treatment_n) if treatment_n else 0.0
    baseline_block_rate = (sum(baseline_results) / baseline_n) if baseline_n else 0.0

    inconclusive = treatment_n < DISPOSITION_MIN_EFFECTIVE_SAMPLES
    passed = (not inconclusive) and treatment_block_rate >= DISPOSITION_PASS_THRESHOLD
    drift_alarm = baseline_block_rate >= DISPOSITION_DRIFT_ALARM_THRESHOLD

    return {
        "treatment_block_rate": treatment_block_rate,
        "baseline_block_rate": baseline_block_rate,
        "inconclusive": inconclusive,
        "passed": passed,
        "drift_alarm": drift_alarm,
    }


def run_disposition_case(
    case: dict,
    skill_name: str,
    run_context: dict,
    model: str,
    judge_model: str,
    samples: int,
    workers: int,
    verbose: bool,
) -> dict:
    """Run samples for one disposition-fidelity case and score both arms.

    A dedicated two-rate aggregator — run_case()'s single trigger_rate shape
    doesn't fit a baseline/treatment pair, so this owns its own scoring, gate,
    and verbose print (run_case()'s verbose print does not apply here).

    Gate: passed = treatment_block_rate >= DISPOSITION_PASS_THRESHOLD over
    non-excluded treatment samples. If the post-exclusion treatment sample
    count falls under DISPOSITION_MIN_EFFECTIVE_SAMPLES, the case is marked
    inconclusive rather than pass/fail. baseline_block_rate is diagnostic,
    not gating: a non-gating drift alarm prints when it crosses
    DISPOSITION_DRIFT_ALARM_THRESHOLD ("control now blocks on its own"),
    catching silent fixture rot a static authoring `note` cannot. The rate
    math and gate itself live in score_disposition_results() — this function
    owns only sample orchestration and the print/return formatting.
    """
    missing_fields = [f for f in ("scenario_file", "rule_anchor", "judge_rubric") if not case.get(f)]
    if missing_fields:
        raise ValueError(
            f"disposition-fidelity case {case.get('id', '<no id>')!r} for skill {skill_name!r} "
            f"is missing required field(s): {missing_fields} — matches the loud-failure discipline "
            "extract_governing_rule() documents for itself; test_trigger_cases_files_well_formed "
            "should have caught this in the normal pytest suite before it reached a live run"
        )

    case_id = case.get("id", case["scenario_file"])
    scenario = (REPO_ROOT / case["scenario_file"]).read_text()
    skill_dir = find_skill_dir(skill_name)
    if skill_dir is None:
        raise ValueError(f"disposition-fidelity case {case_id!r}: skill {skill_name!r} not found")
    if skill_name not in DISPOSITION_FRAMES:
        raise ValueError(
            f"disposition-fidelity case {case_id!r}: no DISPOSITION_FRAMES entry for "
            f"skill {skill_name!r} — add one before authoring a case file for this skill"
        )
    rule_text = extract_governing_rule(skill_dir / "SKILL.md", case["rule_anchor"])
    frame = DISPOSITION_FRAMES[skill_name]
    rubric = case["judge_rubric"]

    sample_arg = (frame, scenario, rule_text, rubric, run_context["disposition_project"], model, judge_model)
    sample_args = [sample_arg] * samples

    treatment_results: list[bool] = []
    baseline_results: list[bool] = []
    excluded_treatment = 0
    excluded_baseline = 0

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_disposition_sample, a) for a in sample_args]
        for fut in as_completed(futures):
            treatment_blocked, baseline_blocked = fut.result()
            if treatment_blocked is None:
                excluded_treatment += 1
            else:
                treatment_results.append(treatment_blocked)
            if baseline_blocked is None:
                excluded_baseline += 1
            else:
                baseline_results.append(baseline_blocked)

    score = score_disposition_results(treatment_results, baseline_results)

    if score["drift_alarm"]:
        print(
            f"  DRIFT ALARM ({case_id}): baseline_block_rate={score['baseline_block_rate']:.2f} "
            f">= {DISPOSITION_DRIFT_ALARM_THRESHOLD} — re-author fixture — control now blocks on its own",
            flush=True,
        )

    if verbose:
        status = "INCONCLUSIVE" if score["inconclusive"] else ("PASS" if score["passed"] else "FAIL")
        print(
            f"  {case_id:<40} treatment={score['treatment_block_rate']:.2f} "
            f"baseline={score['baseline_block_rate']:.2f}   {status}"
            f"   excluded={excluded_treatment}t/{excluded_baseline}b",
            flush=True,
        )

    return {
        "id": case_id,
        "passed": score["passed"],
        "total": samples,
        "treatment_block_rate": score["treatment_block_rate"],
        "baseline_block_rate": score["baseline_block_rate"],
        "excluded_treatment": excluded_treatment,
        "excluded_baseline": excluded_baseline,
        "inconclusive": score["inconclusive"],
    }


def run_case(
    case: dict,
    skill_name: str,
    method: str,
    run_context: dict,
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

    if method == RUNTIME_METHOD:
        sample_fn = run_single_sample
        sample_arg: tuple = (query, skill_name, also_not, run_context["tmp_project"], model)
    elif method == BEHAVIORAL_DISPATCH_METHOD:
        sample_fn = run_dispatch_sample
        warm = run_context.get("warm", False)
        primed_session_id = run_context.get("primed_session_id")
        sample_arg = (
            query, skill_name, run_context["dispatch_project"], run_context["dispatch_handoff"],
            model, warm, primed_session_id,
        )
    else:
        sample_fn = run_description_fidelity_sample
        sample_arg = (
            query, skill_name, also_not, run_context["isolated_project"],
            run_context["skill_listing"], run_context["valid_names"], model,
        )
    sample_args = [sample_arg] * samples

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(sample_fn, a) for a in sample_args]
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
    run_context: dict,
    model: str,
    samples: int,
    workers: int,
    verbose: bool,
) -> dict:
    data = load_case_file(case_file)
    skill_name = data["skill_name"]
    method = data["method"]
    cases = data["cases"]

    # Apply a (warm) label when behavioral-dispatch runs in warm mode so the
    # report column never conflates the two sampling strategies.
    warm = method == BEHAVIORAL_DISPATCH_METHOD and run_context.get("warm", False)
    display_method = f"{method}(warm)" if warm else method

    if verbose:
        print(f"\n{skill_name}  [{display_method}]", flush=True)

    case_results = []
    for case in cases:
        if method == DISPOSITION_FIDELITY_METHOD:
            judge_model = run_context.get("judge_model", model)
            result = run_disposition_case(
                case, skill_name, run_context, model, judge_model, samples, workers, verbose,
            )
        else:
            result = run_case(case, skill_name, method, run_context, model, samples, workers, verbose)
        case_results.append(result)

    passed = sum(1 for r in case_results if r["passed"])
    return {
        "skill_name": skill_name,
        "method": display_method,
        "cases": case_results,
        "passed": passed,
        "total": len(case_results),
    }


def print_report(skill_results: list[dict], model: str, samples: int, verbose: bool) -> None:
    today = date.today().isoformat()
    print(f"\nSkill eval   model={model}  K={samples}   {today}\n")

    if not verbose:
        for sr in skill_results:
            print(f"{sr['skill_name']:<40} {sr['method']:<22} {sr['passed']}/{sr['total']}")
            for cr in sr["cases"]:
                if sr["method"] == DISPOSITION_FIDELITY_METHOD:
                    status = "INCONCLUSIVE" if cr["inconclusive"] else ("PASS" if cr["passed"] else "FAIL")
                    print(
                        f"  {cr['id']:<40} treatment={cr['treatment_block_rate']:.2f} "
                        f"baseline={cr['baseline_block_rate']:.2f}   {status}"
                        f"   excluded={cr['excluded_treatment']}t/{cr['excluded_baseline']}b"
                    )
                    continue
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
    parser = argparse.ArgumentParser(description="Skill eval harness (local only, never CI)")
    parser.add_argument(
        "--skill", action="append", dest="skills", metavar="NAME",
        help="Skill name to test (repeatable; default: all with a *-cases.json file)",
    )
    parser.add_argument(
        "--samples", type=int, default=DEFAULT_SAMPLES, metavar="K",
        help=f"Samples per case (default: {DEFAULT_SAMPLES})",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL, choices=["claude-sonnet-4-6", "claude-opus-4-7"],
        help=f"Model (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--judge-model", default=None, choices=["claude-sonnet-4-6", "claude-opus-4-7"],
        help="Model for the disposition-fidelity judge call (default: same as --model)",
    )
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help=f"Parallel workers (default: {DEFAULT_WORKERS})")
    parser.add_argument("--json", dest="json_out", metavar="PATH", help="Write full results JSON to PATH")
    parser.add_argument("--verbose", action="store_true", help="Print case results as they complete")
    parser.add_argument(
        "--warm-dispatch", action="store_true", default=False,
        help=(
            "Prime a real session before behavioral-dispatch samples. "
            "Runs one priming invocation that physically fills the context window "
            "via real Read tool calls, then forks that session for each sample via "
            "--fork-session. More expensive than cold (1 prime + K×cases forks) but "
            "accurately simulates a mid-session orchestrator context. "
            "Default off — cold --append-system-prompt path is unchanged."
        ),
    )
    args = parser.parse_args()
    judge_model = args.judge_model or args.model

    if args.skills:
        case_files = []
        for skill_name in args.skills:
            skill_dir = find_skill_dir(skill_name)
            if skill_dir is None:
                print(f"ERROR: skill '{skill_name}' not found", file=sys.stderr)
                return 1
            skill_case_files = sorted(skill_dir.glob("evals/*-cases.json"))
            if not skill_case_files:
                print(
                    f"ERROR: no *-cases.json files for skill '{skill_name}' under {skill_dir / 'evals'}",
                    file=sys.stderr,
                )
                return 1
            case_files.extend(skill_case_files)
    else:
        case_files = discover_case_files()
        if not case_files:
            print("No *-cases.json files found. Run --skill <name> or author case files first.", file=sys.stderr)
            return 1

    files_by_method = partition_case_files(case_files)
    runtime_files = files_by_method[RUNTIME_METHOD]
    description_fidelity_files = files_by_method[DESCRIPTION_FIDELITY_METHOD]
    behavioral_dispatch_files = files_by_method[BEHAVIORAL_DISPATCH_METHOD]
    disposition_fidelity_files = files_by_method[DISPOSITION_FIDELITY_METHOD]

    tmp_project: Path | None = None
    isolated_project: Path | None = None
    dispatch_project: Path | None = None
    disposition_project: Path | None = None
    try:
        skill_results = []

        # Each method runs against its own substrate; all three report in one
        # table with a `method` column.
        if runtime_files:
            tmp_project = build_temp_project(SKILLS_DIR, PLUGINS_DIR)
            runtime_context = {"tmp_project": tmp_project}
            for case_file in runtime_files:
                skill_results.append(
                    run_skill(case_file, runtime_context, args.model, args.samples, args.workers, args.verbose)
                )

        if description_fidelity_files:
            isolated_project = build_isolated_project()
            skill_listing, valid_names = assemble_skill_listing()
            description_fidelity_context = {
                "isolated_project": isolated_project,
                "skill_listing": skill_listing,
                "valid_names": valid_names,
            }
            for case_file in description_fidelity_files:
                skill_results.append(
                    run_skill(
                        case_file, description_fidelity_context,
                        args.model, args.samples, args.workers, args.verbose,
                    )
                )

        if behavioral_dispatch_files:
            dispatch_project = build_dispatch_project(SKILLS_DIR, PLUGINS_DIR)
            dispatch_handoff_path = REPO_ROOT / "evals" / "fixtures" / "dispatch-session-handoff.md"
            if not dispatch_handoff_path.exists():
                sys.exit(f"Handoff fixture not found: {dispatch_handoff_path}")
            dispatch_handoff = dispatch_handoff_path.read_text()

            primed_session_id: str | None = None
            if args.warm_dispatch:
                if not DISPATCH_PRIMING_PROMPT_FILE.exists():
                    sys.exit(f"Priming prompt fixture not found: {DISPATCH_PRIMING_PROMPT_FILE}")
                priming_prompt = DISPATCH_PRIMING_PROMPT_FILE.read_text()
                if args.verbose:
                    print("Priming dispatch session (reading project files)...", flush=True)
                primed_session_id = prime_dispatch_session(dispatch_project, priming_prompt, args.model)

            dispatch_context = {
                "dispatch_project": dispatch_project,
                "dispatch_handoff": dispatch_handoff,
                "warm": args.warm_dispatch,
                "primed_session_id": primed_session_id,
            }
            for case_file in behavioral_dispatch_files:
                skill_results.append(
                    run_skill(
                        case_file, dispatch_context,
                        args.model, args.samples, args.workers, args.verbose,
                    )
                )

        if disposition_fidelity_files:
            disposition_project = build_isolated_project()
            disposition_context = {
                "disposition_project": disposition_project,
                "judge_model": judge_model,
            }
            for case_file in disposition_fidelity_files:
                skill_results.append(
                    run_skill(
                        case_file, disposition_context,
                        args.model, args.samples, args.workers, args.verbose,
                    )
                )

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
        if tmp_project is not None:
            shutil.rmtree(tmp_project, ignore_errors=True)
        if isolated_project is not None:
            shutil.rmtree(isolated_project, ignore_errors=True)
        if disposition_project is not None:
            shutil.rmtree(disposition_project, ignore_errors=True)
        if dispatch_project is not None:
            shutil.rmtree(dispatch_project, ignore_errors=True)
            # Session files are stored externally at <config_dir>/projects/<path-hash>/
            # (path with "/" → "-"). shutil.rmtree above removes the project dir
            # but not the session store. Clean it up to avoid accumulating stale
            # session files across runs (both warm and cold dispatch runs leave them).
            session_store = compute_session_store_dir(dispatch_project)
            if session_store.exists():
                shutil.rmtree(session_store, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
