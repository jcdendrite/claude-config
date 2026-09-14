---
name: transcript-narrative
description: Produce a narrative case study / annotated timeline from Claude Code session transcripts — verbatim prompts, phase buckets, quantitative metrics, extracted lessons. For raw quantitative metrics use transcript-analysis.
context: fork
background: false
argument-hint: "[optional output path]"
---

## Step 0 — Confirm Bash is available

This skill runs as a forked context and depends on `Bash` for every step below. If `Bash` is unavailable, stop and report that this skill requires Claude Code v2.1.218 or later — an earlier version honors `context: fork` without honoring `background: false`, producing a background fork whose narrowed tool set may omit `Bash`.

This skill never invokes `marker.sh` and never invokes a review skill, directly or by dispatching a subagent to do either on its behalf.

## Step 1 — Scope the analysis

Identify the branches, session date-range, and repos in play. Use `buckets` to enumerate branches and per-branch models:

```bash
python3 ~/.claude/scripts/transcript-analysis.py buckets --this-repo
```

Note the branch names — you will pass them to later subcommands via `--branches <branch>`.

## Step 2 — Extract verbatim user turns

Get the transcript file set and read only those files:

```bash
python3 ~/.claude/scripts/transcript-analysis.py sessions --paths --include-subagents
```

`sessions --paths` prints its resolved-scope header (`SESSIONS SOURCES (...)`) to stderr — record that line. This invocation passes no `--projects`, so its scope label is always `*`; the root-count clause is what tells you whether the corpus covers every declared account or just one.

Read each returned path directly with the Read tool. Do not vendor a script or hand-roll a shell expression or glob for this step — `sessions --paths` is the single source of the file set. Check the `SESSIONS SOURCES (...)` line from Step 2: if its root-count clause reads "1 root (no ~/.claude/transcript-config-dirs declared)", the corpus covers only the active account — say so in the case study rather than assuming every declared account was scanned. From the records you read, filter to turns where:
- `role == "user"`
- `isSidechain` is absent or `false` — exclude subagent/sidechain turns entirely

Capture BOTH opening prompts AND mid-session steering turns. Later corrective or redirecting turns are often as instructive as the opener.

For each qualifying turn, record:
- Session ID and timestamp
- `message.content[].text` — the verbatim prompt text

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

## Step 5 — Redact before sharing or persisting

Quoted prompts come from direct JSONL extraction in Step 2 — the CLI's `--redact` flag is available only on `audit-routing` and does not touch them. Run this scan before Step 7 writes the artifact to disk, not only before publishing to a shared or public surface:

- Manually scan every quoted prompt for customer PII and credentials
- For the quantitative appendix, use `audit-routing --redact` to anonymize project-dir names when posting output to GitHub issues or external surfaces

## Step 6 — Extract lessons, ranked by prompt-arc visibility

Distill lessons from the annotated timeline. For each lesson:
- Tie it to the verbatim prompt arc that surfaced it (cite the phase and the specific prompt)
- Rank by how clearly the lesson appears in the evidence — lessons with a direct, traceable prompt arc rank above those inferred from metrics alone

## Step 7 — Write the artifact and return the path

Write the annotated timeline (Step 3), the quantitative appendix (Step 4), and the ranked lessons (Step 6) to the output path given as this skill's argument. When no path is given, create one under `mktemp -d` and write there instead — state plainly to the caller that this default location is temporary.

Before writing to a caller-supplied path, confirm it does not resolve inside a git-tracked tree unless that tree's `.gitignore` covers it: `mktemp -d` creates its directory at mode `0700`, but a caller-supplied path carries no such guarantee, and a git-tracked destination without `.gitignore` coverage risks committing the file's content, including anything Step 5's manual scan missed.

Return only the output path and the ranked lessons from Step 6 — not the annotated timeline or the quantitative appendix inline. A follow-up question that needs that detail re-opens the file at the returned path.

