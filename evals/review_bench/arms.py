"""Arm construction for the review bench: the current-rule and
function-context `bench-<lens>.md` files, snapshotted from
production and installed into a fixture's `.claude/agents/`.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
AGENTS_DIR = REPO_ROOT / "claude" / ".claude" / "agents"

ARM_CURRENT_RULE = "current-rule"
ARM_FUNCTION_CONTEXT = "function-context"
KNOWN_ARMS: frozenset[str] = frozenset({ARM_CURRENT_RULE, ARM_FUNCTION_CONTEXT})

# Every arm's tool allowlist: production's Read/Grep/Glob subset, with Bash
# and Write removed. A `tools:` allowlist withholds an unlisted tool from the
# subagent outright. An arm therefore cannot run Bash or Write regardless of
# ambient CLI permission behavior.
ARM_TOOLS: tuple[str, ...] = ("Read", "Grep", "Glob")

# model: inherit, so that --model <frozen reviewer ID> governs the dispatched
# bench-<lens> subagent -- inherit resolves to the dispatching session's own
# active model as long as the dispatcher's own Agent tool call omits an
# explicit model parameter. Only the judge agent files pin a literal model ID
# instead.
ARM_MODEL_FRONTMATTER_VALUE = "inherit"

# The seven lenses with a read clause to substitute, each mapped to the exact
# clause verified to occur exactly once in that lens's current production
# body. Any other duty in the same sentence as the clause (e.g. staff-sdet's
# "AND the code they test") is not part of the clause, and stays in the body
# untouched after the function-context substitution.
LENS_READ_CLAUSES: dict[str, str] = {
    "staff-backend-engineer": "Read every changed file fully",
    "staff-frontend-engineer": "Read every changed component and hook (or composable / reactive primitive) fully",
    "staff-sdet": "Read changed tests fully",
    "staff-platform-engineer": "Read every changed pipeline/IaC/script file fully",
    "staff-analytics-engineer": "Read every changed model and transformation fully",
    "ciso-reviewer": "Read every changed file fully",
    "comment-discipline-reviewer": "Read every changed file fully",
}

# The function-context arm's read-rule clause -- kept verbatim, character for
# character, wherever it's substituted.
FUNCTION_CONTEXT_CLAUSE = (
    "Read the change through its function-context diff "
    "(`.bench/change-function-context.diff`), not by reading changed files "
    "whole; for any other context you need, locate it with Grep and read "
    "only that range. This read rule overrides any general instruction to "
    "read a whole file when reviewing it."
)


class ArmSnapshotError(ValueError):
    """Raised when a production lens file fails one of snapshot_arm's
    loud-failure checks: a missing ARM_TOOLS entry, or (for the function-context arm) a
    read clause that doesn't match exactly once."""


_FRONTMATTER_FIELD_RE = re.compile(r"(?m)^(\w+):\s*(.*)$")


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Return (frontmatter_block, body); frontmatter_block excludes the
    delimiting '---' lines. Raises ValueError when text has no closed
    leading frontmatter block."""
    if not text.startswith("---"):
        raise ValueError("agent file has no leading frontmatter block")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("agent file's frontmatter block is never closed")
    frontmatter = text[3:end].strip("\n")
    body = text[end + 4:]
    return frontmatter, body


def _parse_tools_field(frontmatter: str) -> frozenset[str]:
    match = re.search(r"(?m)^tools:\s*(.+?)\s*$", frontmatter)
    if not match:
        return frozenset()
    return frozenset(t.strip() for t in match.group(1).split(",") if t.strip())


def _replace_frontmatter_field(frontmatter: str, field: str, value: str) -> str:
    """Replace field's value in place when present, else append it as a new
    line -- preserves every other field's byte-identical text and relative
    order (the "arm files differ from production only in name, model,
    tools, and the substituted clause" invariant)."""
    pattern = re.compile(rf"(?m)^{re.escape(field)}:.*$")
    if pattern.search(frontmatter):
        return pattern.sub(f"{field}: {value}", frontmatter, count=1)
    return frontmatter + f"\n{field}: {value}"


def _substitute_read_clause(body: str, lens: str) -> str:
    clause = LENS_READ_CLAUSES[lens]
    count = body.count(clause)
    if count != 1:
        raise ArmSnapshotError(
            f"{lens}: expected its read clause {clause!r} to occur exactly once in the "
            f"production agent body, found {count}"
        )
    return body.replace(clause, FUNCTION_CONTEXT_CLAUSE, 1)


def _production_agent_text(lens: str, agents_dir: Path) -> str:
    return (agents_dir / f"{lens}.md").read_text()


def render_arm_agent(arm: str, lens: str, *, agents_dir: Path = AGENTS_DIR) -> str:
    """Render one lens's `bench-<lens>.md` text for `arm` from its
    production agent file under `agents_dir`.

    Fails loudly (ArmSnapshotError) unless production's `tools:` holds
    every ARM_TOOLS entry, and, for ARM_FUNCTION_CONTEXT, unless the lens's
    read clause matches exactly once in the production body.
    """
    if arm not in KNOWN_ARMS:
        raise ValueError(f"unknown arm {arm!r}, expected one of {sorted(KNOWN_ARMS)}")
    if lens not in LENS_READ_CLAUSES:
        raise ValueError(f"{lens!r} has no known read clause -- not one of the seven lenses the review bench covers")

    text = _production_agent_text(lens, agents_dir)
    frontmatter, body = _split_frontmatter(text)

    declared_tools = _parse_tools_field(frontmatter)
    missing_tools = set(ARM_TOOLS) - declared_tools
    if missing_tools:
        raise ArmSnapshotError(
            f"{lens}: production tools: {sorted(declared_tools)} is missing {sorted(missing_tools)} -- "
            f"an arm can never gain a tool its lens lacks"
        )

    if arm == ARM_FUNCTION_CONTEXT:
        body = _substitute_read_clause(body, lens)

    frontmatter = _replace_frontmatter_field(frontmatter, "name", f"bench-{lens}")
    frontmatter = _replace_frontmatter_field(frontmatter, "model", ARM_MODEL_FRONTMATTER_VALUE)
    frontmatter = _replace_frontmatter_field(frontmatter, "tools", ", ".join(ARM_TOOLS))

    # body already retains the closing fence's own trailing newline (see
    # _split_frontmatter) -- no "\n" is inserted here, or the reconstructed
    # text would gain a byte-for-byte extra blank line production never had.
    return f"---\n{frontmatter}\n---{body}"


def snapshot_arm(arm: str, lenses: list[str] | None = None, *, agents_dir: Path = AGENTS_DIR) -> dict[str, str]:
    """Render every lens's `bench-<lens>.md` text for `arm`. Returns
    {lens: rendered_agent_file_text}. See render_arm_agent for the
    per-lens loud-failure checks."""
    lens_names = lenses if lenses is not None else sorted(LENS_READ_CLAUSES)
    return {lens: render_arm_agent(arm, lens, agents_dir=agents_dir) for lens in lens_names}


def install_arm(rendered: dict[str, str], agents_dir: Path) -> None:
    """Write each lens's rendered `bench-<lens>.md` into `agents_dir`. Runs
    copy the snapshot files themselves (runner.build_defect_fixture_spec)."""
    agents_dir.mkdir(parents=True, exist_ok=True)
    for lens, text in rendered.items():
        (agents_dir / f"bench-{lens}.md").write_text(text)


def write_arm_snapshot(arm: str, dest_dir: Path, *, lenses: list[str] | None = None) -> None:
    """`snapshot-arms`' own entry point, run once at freeze time: render and
    write every lens's `bench-<lens>.md` directly under `dest_dir`
    (evals/review_bench/arms/<arm>/), the committed snapshot
    runner.build_defect_fixture_spec copies into each run's fixture."""
    install_arm(snapshot_arm(arm, lenses), dest_dir)
