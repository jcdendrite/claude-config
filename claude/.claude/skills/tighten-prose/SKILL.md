---
name: tighten-prose
description: Rewrite prose for clarity and concision — short sentences, active voice, one idea per sentence, consistent terminology — without dropping or softening any fact, number, decision, or hedge. Operates on a drafted PR body, handoff note, or literal input text. Dispatched by /pr-description's prose-tightening pass; also invocable standalone any time drafted text needs the same treatment.
---

# Tighten Prose

## 1. Scope: drafted prose, not durable in-repo content

Operates on PR bodies, handoff notes, and text the invoker hands over
directly. Do not rewrite code comments, `REFERENCES.md`, doc files, README
sections, skill or agent bodies, or a plan file under `.claude/plans/` — that
prose is judged by a different standard, where the required action is to
name the violating site, not rewrite it (see `comment-discipline-reviewer`).
Invoked against a durable doc or a plan file, say so and stop. A caller may
still name a specific section of a non-plan durable doc to tighten, and then
only that section is in scope — a plan file is never in scope, not even by
named section.

## 2. Preserve every fact and its original strength

The overriding constraint, ahead of every rule below: rewrite phrasing and
structure only. Never delete, merge away, or soften/harden a claim, number,
decision, hedge, or conditional the input stated. When a rewrite would need
to drop or flatten something to shorten it, keep the content and accept the
longer sentence instead.

## 3. Carve-outs, left untouched verbatim

- **Syntactic** — fenced code blocks, inline code spans, file paths,
  identifiers, proper nouns (tool/library/repo names), numbers and units,
  markdown headings, markdown tables, any machine-managed delimited block
  (e.g. `<!-- pr-cost:start -->`/`:end`).
- **Semantic** (undetectable by pattern-matching, so excluded rather than
  trusted to a rewrite-then-check loop) — hedges and modal verbs ("may,"
  "should," "could," "is likely to"), quantifiers ("some," "most," "all"),
  negation scope, and conditional clauses ("if X, then Y"). Do not reword a
  sentence carrying any of these; leave the whole sentence as-is.
- **Whole-sentence-class** — deploy/coordination steps, security-invariant
  claims, and reviewer-action items. Leave these sentences untouched even
  when they're otherwise verbose. `pr-description`'s own "Coordination-step
  preservation" section already treats this content as high-stakes; this
  skill defers to that judgment rather than re-deciding it.

## 4. Rewrite rules

For everything outside the carve-outs above, each rule stands on its own
rationale:

- Active voice, except when the actor is unknown or irrelevant to the
  reader.
- Target ~20-25 words per sentence.
- One idea per sentence — split compound sentences joined by "and" that
  carry two separate, unconditional claims. Never split a sentence
  containing a carved-out conditional.
- Pick one term per concept and keep it for the whole document — no elegant
  variation.
- Avoid noun-stack phrases — rewrite into a verb phrase or prepositional
  phrase.
- Prefer plain, common verbs over inflated ones ("start" not "commence").

## 5. Input handling

Given a file path: `Read` it, apply the rewrite, `Edit` it in place, and
report the actual changed lines — a diff-shaped before/after list, not a
prose summary. A summary can't be checked by the reader; the real lines can.

Given literal text (no existing file): return the rewritten text inline.

## 6. Self-check before returning

Re-read input and output side by side, sentence by sentence. Confirm every
fact, number, decision, and hedge/conditional strength in the input matches
the output. This is a secondary net for ordinary prose — the carve-outs
above are the primary defense for the highest-risk sentence classes, not
this check.
