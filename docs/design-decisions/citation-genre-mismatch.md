# `skill-fidelity-reviewer`'s low cited-path edit rate is a citation-genre mismatch, not a reviewer-value signal

*2026-08-30. Formerly `docs/design-decisions.md` §33.*

The corrected `reviewer-yield` measurement (GH-762, PR #764) puts `skill-fidelity-reviewer`'s zero-finding-bucket cited-path edit rate well below every peer reviewer's. [§9](reviewer-persona-roster-operations.md) established this agent's charter and its own findings-rate re-measurement instruction; it contains no discussion of the cited-path column, so citing it for why that rate is expected is a misattribution. This entry, not [§9](reviewer-persona-roster-operations.md), is the record's home for the cited-path reasoning below.

**Mechanism 1: the join key is lexical and hashed, so even the one branch shape where the citation is the work surface still cannot register.** `skill-fidelity-reviewer` resolves each skill by reading `~/.claude/skills/<name>/SKILL.md` (its Name resolution step) — a config-dir path. A branch in this repo that edits that same skill edits it through the stow source, `claude-skills/skills/<name>/SKILL.md`. `_normalize_cited_path` is deliberately lexical — no `Path.resolve()`, `os.path.realpath`, or `stat` — and hashes the normalized string to a sha256 prefix. Two spellings of the same file produce two different keys and never join, so a branch that genuinely edits the cited skill in response to a finding still would not register as an edited cited path.

**Mechanism 2: on a clean pass the scanned output names specifications, not the branch's work surface.** Citations are drawn from the last assistant text plus every `Write` blob. With `findings_path` set, the inline return is a one-line pointer, and the substance lives in the findings file. On a clean pass, that file's content is a dismissal list naming skills and skill bodies — the specs the agent read, not the diff it was handed. A branch is not normally editing the skill it invoked, which is what keeps the numerator small.

Dispatch timing then inflates the denominator without touching that numerator. `/ready-for-review` spawns this agent once per branch at the last gate before handoff. The ship path at that point still produces real edits, but they are edits fixing other reviewers' findings, not this reviewer's own citations. `Active`, defined as "the session recorded any code edit at all," is a null control, not a path-specific one, so it is easily satisfied while the cited spec paths stay untouched.

Together this means the cross-reviewer `Rate` comparison is not like-for-like. A `staff-*` reviewer carries `Bash`, re-fetches the diff inside its own context, and cites the diff's own files — the files the session edits next. `skill-fidelity-reviewer` carries `Read, Grep, Glob, Write`, no `Bash`, and cites the specifications it checked the diff against. Ranking the two against each other measures citation genre, not reviewer value, so [§25](skill-fidelity-reviewer-widened-scope.md)'s scope-widening decision and [§9](reviewer-persona-roster-operations.md)'s charter and re-measurement instruction stand unchanged: no routing, trigger-prose, or dispatch-condition change follows from this rate.

**Falsifier.** Cited paths are held only as sha256 digests and never surface as raw paths, so mechanism 2 cannot be checked against the tool's own output. It can be checked against the agent's own findings files under `agent-reviews/` in existing worktrees: if clean-pass findings files routinely name the branch's own diff paths rather than the skills and specs read, mechanism 2 is wrong and the low rate becomes a real "the session ignored what it was told" signal. The same follows if the agent's Output format later gains a required enumeration of files checked. Either observation reopens this entry.

## Sources

- `claude/.claude/scripts/transcript_analysis/reviewer_yield.py` — `_normalize_cited_path` (lexical, hashed join key) and `_reviewer_yield_cited_keys` (citation candidates drawn from the last assistant text and every `Write` blob).
- `claude/.claude/agents/skill-fidelity-reviewer.md` — Name resolution (config-dir path reads); Output format (findings-file substance on a clean pass).
- `claude-skills/skills/ready-for-review/SKILL.md` — the once-per-branch dispatch step.
- `docs/transcript-analysis.md`'s `reviewer-yield` section — the `Cited`/`Active`/`Edited`/`Rate` column definitions and the digest-only redaction note.
- GH-762 / PR #764 — the reviewer-yield measurement fix this observation post-dates.
