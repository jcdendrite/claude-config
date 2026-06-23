---
name: root-cause-analysis
description: "A playbook for debugging and root-cause investigation: establish the full symptom, verify tool ingestion, reproduce and capture the asymmetry, pull data for the specifically affected entities, then confirm the fix addresses the reported incident before shipping."
---

# Root-Cause Investigation Playbook

Work through the stages below in order. Do not advance past a stage until its condition is satisfied. Maintain an **open questions** list throughout — each unanswered question goes here, each resolved question is closed with the finding.

## Stage A — Establish the full symptom

Before forming any hypothesis, read the complete incident report. Two plausible hypotheses aimed at the wrong symptom produce the wrong fixes — both can look correct in isolation.

- What exactly is failing, for whom, and since when?
- What is NOT failing? Identifying the boundary sharpens the scope.
- Add every unknown to the open questions list. Do not proceed to Stage B until the full symptom is captured.

## Stage B — Verify tool ingestion

Silent truncation is invisible. A document reader, paginated query, or large-context fetch that stops early looks identical to a complete read.

- For every large input processed during investigation: confirm how many pages, rows, or items were returned. Ask "what is on the last one?"
- If the tool's output count does not match the expected total, the analysis is built on incomplete input — restart from a complete read before continuing.

## Stage C — Reproduce and capture the asymmetry

"It fails when I do X but not Y" is not noise — it is the root cause's fingerprint. The asymmetry points directly at the code branch the bug must pass through.

- Reproduce the failure reliably. Record the exact conditions.
- Identify what is different between the failing case and the passing case. State the asymmetry precisely.
- A green test suite confirms "this code behaves as specified" — not "this code fixes the reported bug." Trace the symptom to a root cause before writing the first line of code.

## Stage D — Pull data for the affected entities

A real bug in the right subsystem that affects a different population is not the reported bug.

- Pull records for the specifically reported users, items, or entities.
- Inspect every field in the returned data — especially unfamiliar status values. Do not rely on a summary; read the raw values yourself.
- Screenshots and error states in a report are engineering data. Read every label and value: "why would this field show this value?" An unexpected display value is often a direct artifact of corrupted underlying data.

## Stage E — Form a hypothesis

With full symptom, verified input, a captured asymmetry, and entity-level data in hand, form a single, specific hypothesis:

- What state is wrong, and what path set it wrong?
- What would the system look like if the hypothesis is correct? (Verify that against the Stage D data.)
- Distinguish "a real bug in the right area" from "the reported bug." An investigation will surface real fires to put out; the original fire still burns until the reported symptom is addressed.

## Stage F — Confirm this incident before shipping

Before declaring the fix complete:

- Verify that the fix would have changed the state of the specifically reported entities.
- If you cannot confirm this, return to Stage D and pull the data.
- Name explicitly when investigation sessions are drifting across repos without a principled root cause. When that happens: stop, acknowledge that prior fixes may be orthogonal, and run the playbook from Stage A before any further implementation.

## Open questions / what we still don't know

Maintain this list as a live artifact across sessions. Handoff files carry implementation state reliably but silently drop investigative prerequisites. An explicit list of what is not yet known, distinct from what has been built, prevents sessions from converging on the wrong fix.
