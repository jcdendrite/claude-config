---
name: measure-check-output
description: >
  Two-half procedure for grounding a harness-truncation or check-suite-size
  claim in a measured, dated figure instead of an assumed one. Half 1
  re-probes the Claude Code Bash-tool output-truncation threshold and
  preview size by bisection. Half 2 measures a project's own documented
  check commands against that threshold, one project at a time, using two
  single-purpose Bash calls per command so no raw suite output enters
  context. Invoke by name when a harness-limit figure looks stale, or when
  a project needs its own check-suite-size baseline before writing an
  inline-vs-delegate rule that depends on one.
user-invocable: true
---

# Measure check output

This is a measurement procedure, not a policy. It produces a figure with its
own basis attached; it does not decide whether to delegate a check run — that
decision lives in `subagent-delegation/SKILL.md`. Before writing down any
figure this procedure produces, state which half produced it and what it was
measured against — a byte count with no stated basis is exactly the
undated-literal defect this procedure exists to avoid.

## Half 1 — re-probe the harness output limits

This half is irreducibly model-run: what is being measured is what the Bash
tool returns to *this* model's own context, which no script can observe about
its own invocation.

1. Bisect the truncation threshold: run a command that emits an output of a
   known byte size, and observe whether the harness replaced the returned
   result with a truncation preview. Narrow the known size until the boundary
   is pinned to an exact byte count.
2. Byte-count what actually came back in context to get the preview size —
   don't estimate it from the truncation message, which may report a
   different unit (KiB vs. decimal bytes) than the byte-for-byte figure.
3. Compare the returned preview against the full persisted output to
   establish whether the preview is the head or the tail of what ran.
4. Confirm where the harness persists the overflow (the path pattern, and
   whether the model is expected to auto-read it or must go fetch it).

## Half 2 — measure a project's own check commands

Read the project's documented check commands from its own README or
CLAUDE.md — never guess which commands count toward "the check suite." An
umbrella command (`npm run verify`, `make check`) can wrap several
sub-suites whose individual counts are not visible from the umbrella's own
name. Never invent a per-sub-suite breakdown from partial output — that is
the failure `docs/case-studies/check-runner.md` §
"Incident 3 — invented sub-suite test counts (2026-05-15)" records.

Per documented command, use two separate single-purpose Bash calls — never
one call that both runs the command and inspects its output:

1. Run the command with its output redirected to a file (`> <file> 2>&1`).
   No suite output enters context from this call.
2. In a second call, `wc -c` the file for the byte count, and `grep` the
   runner's own summary line (the line stating pass/fail/skip counts) to
   quote verbatim. Do not `cat`, `head`, or `Read` the file whole — that
   defeats the point of redirecting in the first call. Never characterize
   the summary line in your own words when you could quote it. `docs/case-studies/check-runner.md`
   § "Incident 5 — count over-reporting recurs (2026-05-19)" is the record of
   a summarized count diverging from the runner's own line once free-form
   text stood between the model and the source of truth.

Report each command's byte count and that count as a percentage of the
threshold Half 1 established.

## Record as-of

Every figure this procedure produces carries four qualifiers: the byte
count, the suite size it was measured against (the quoted summary line),
the date, and the Claude Code version. A figure with any qualifier missing
is not yet safe to write down.

This repo's own figures live in `subagent-delegation/REFERENCES.md` §
"Heavy command output — harness truncation and check-suite sizes" — add to
that section rather than restating it elsewhere. A consuming project has no
such file; it records its own figures in its own repo, wherever that
project keeps the rule the figure grounds.
