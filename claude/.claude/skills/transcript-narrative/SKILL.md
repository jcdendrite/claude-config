---
name: transcript-narrative
description: Produce a narrative case study / annotated timeline from Claude Code session transcripts — verbatim prompts, phase buckets, quantitative metrics, extracted lessons. For raw quantitative metrics use transcript-analysis.
---

## Step 1 — Scope the analysis

Identify the branches, session date-range, and repos in play. Use `buckets` to enumerate branches and per-branch models:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo
```

Note the branch names — you will pass them to later subcommands via `--branches <branch>`.

## Step 2 — Extract verbatim user turns

Read the session JSONL transcripts at `~/.claude/projects/*/`. Each file is one session. Filter to turns where:
- `role == "user"`
- `isSidechain` is absent or `false` — exclude subagent/sidechain turns entirely

Capture BOTH opening prompts AND mid-session steering turns. Later corrective or redirecting turns are often as instructive as the opener.

For each qualifying turn, record:
- Session ID and timestamp
- `message.content[].text` — the verbatim prompt text

Do not vendor a script for this extraction. Read the JSONL directly with the Read tool or a short inline shell expression, filtering by the criteria above.

## Step 3 — Bucket prompts into phases and build an annotated timeline

Group the extracted turns by branch and investigative phase. For each phase, record:

| Field | Source |
|---|---|
| Date range | Timestamps from Step 2 turns |
| Session count | Count of distinct session IDs in the phase |
| Active minutes | `duration --branches <branch>` → `Active(min)` column |
| Artifact produced | PR number or branch name |
| 1–3 most consequential verbatim prompts | Selected from Step 2 extractions |

Prefer prompts that represent a decision point, a course-correction, or a constraint the model had to internalize — not routine status checks.

## Step 4 — Cross-reference quantitative metrics

Run these subcommands and include their output as a quantitative appendix that grounds the narrative:

```bash
# Test-failure convergence vs thrashing
python3 ~/.claude/scripts/transcript-analysis.py fail-seq --branches <branch>

# Active vs idle time
python3 ~/.claude/scripts/transcript-analysis.py duration --branches <branch>

# Subagent vs main-thread split
python3 ~/.claude/scripts/transcript-analysis.py subagents --branches <branch>

# Branch → PR mapping
python3 ~/.claude/scripts/transcript-analysis.py pr-link --repo owner/repo --branches <branch>

# Review-skill invocations, hook denials, reviewer-spawn timeline
python3 ~/.claude/scripts/transcript-analysis.py review-trace --this-repo
```

For `fail-seq` interpretation: a spike followed by zeros is convergent (expected); oscillation with no sustained run of zeros is thrashing (flag for the lessons step). See `transcript-analysis` for the full reading guide.

## Step 5 — Redact before sharing

Quoted prompts come from direct JSONL extraction in Step 2 — the CLI's `--redact` flag is available only on `audit-routing` and does not touch them. Before publishing to any shared or public surface:

- Manually scan every quoted prompt for customer PII and credentials
- For the quantitative appendix, use `audit-routing --redact` to anonymize project-dir names when posting output to GitHub issues or external surfaces

## Step 6 — Extract lessons, ranked by prompt-arc visibility

Distill lessons from the annotated timeline. For each lesson:
- Tie it to the verbatim prompt arc that surfaced it (cite the phase and the specific prompt)
- Rank by how clearly the lesson appears in the evidence — lessons with a direct, traceable prompt arc rank above those inferred from metrics alone

For raw quantitative metrics without narrative synthesis, use `transcript-analysis` — the two skills are designed to be used together.
