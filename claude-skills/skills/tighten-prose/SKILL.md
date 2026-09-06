---
name: tighten-prose
description: Rewrite prose for clarity and concision — short sentences, active voice, one idea per sentence, consistent terminology — without dropping or softening any fact, number, decision, or hedge. Operates on a drafted PR body, handoff note, or literal input text by default; rewrites durable in-repo content only when the invocation names it. Dispatched by /pr-description's prose-tightening pass; also invocable standalone any time drafted text needs the same treatment.
---

# Tighten Prose

## 1. Scope and criteria

Default target: drafted prose — PR bodies, handoff notes, and text the invoker
hands over directly. The `pr-description` pass that dispatches this skill names
a drafted body and nothing else, so the automatic path never reaches in-repo
content.

Durable in-repo content — code comments, `REFERENCES.md`, doc files, README
sections, skill and agent bodies — is in scope only when the invocation names
the file or the section, and then only what it named. Tightening such a file
does not stand in for comment-discipline review: this skill never drops
content, and that review's usual fix is to cut or relocate it. In a skill or
agent body, keep the imperative second-person voice those files are reviewed
against; this skill's rules do not encode it.

Never edit a plan file under `.claude/plans/` in place, named or not.
`require-plan-review.sh` gates `Write`/`Edit`/`MultiEdit` on the active plan
set, so the edit is either denied outright or — inside `/plan-review`'s bypass
window — records a review marker over bytes no reviewer read. Return the
rewritten plan text inline instead, and say that `/plan-review` still has to
cover whatever gets applied.

Criteria, not territory, separate this skill from `comment-discipline-reviewer`.
This skill changes how a sentence reads and never what it says, so its output
can run longer than its input. That reviewer decides whether the content earns
its place — a paragraph carrying one line's worth of fact, prose at the wrong
altitude for its reader, labels that stop parsing once the PR description is
gone — and names each site instead of rewriting it. A file can need both.

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
- **Semantic** (undetectable by pattern-matching, so bounded by an explicit
  rule rather than trusted to a rewrite-then-check loop) — hedges and modal
  verbs ("may," "should," "could," "is likely to"), quantifiers ("some,"
  "most," "all"), negation, and conditional clauses ("if X, then Y"). Keep
  each such word verbatim and keep the span of words it governs unchanged.
  Word-level substitution inside the sentence is still allowed, because it
  leaves that span intact: swap an inflated verb for a plain one, and apply
  the document's chosen term for a concept. Structure is not: do not split,
  merge, or reorder the sentence, do not shorten it toward the
  sentence-length target below, and convert passive to active only when the
  sentence carries no negation and no quantifier — moving the subject across
  either one changes what it covers.
- **Whole-sentence-class** — deploy/coordination steps, security-invariant
  claims, and reviewer-action items. Leave these sentences untouched even
  when they're otherwise verbose. `pr-description`'s own "Coordination-step
  preservation" section already treats this content as high-stakes; this
  skill defers to that judgment rather than re-deciding it.

## 4. Rewrite rules

Outside the carve-outs above, every rule below applies. Inside a semantic
carve-out, only the plain-verb and one-term-per-concept rules apply, on that
carve-out's terms. Each rule stands on its own rationale:

- Active voice, except when the actor is unknown or irrelevant to the
  reader.
- Target ~20-25 words per sentence.
- One idea per sentence — split compound sentences joined by "and" that
  carry two separate, unconditional claims. Never split a sentence carrying
  a semantic carve-out.
- Pick one term per concept and keep it for the whole document — no elegant
  variation.
- Avoid noun-stack phrases — rewrite into a verb phrase or prepositional
  phrase.
- Prefer plain, common verbs over inflated ones ("start" not "commence").

## 5. Input handling

Given a file path: `Read` it, apply the rewrite, `Edit` it in place, and
report the actual changed lines — a diff-shaped before/after list, not a
prose summary. A summary can't be checked by the reader; the real lines can.

Given a plan file path: `Read` it and return the rewritten text inline (§1) —
never `Edit` it.

Given literal text (no existing file): return the rewritten text inline.

## 6. Self-check before returning

Re-read input and output side by side, sentence by sentence. Confirm every
fact, number, decision, and hedge/conditional strength in the input matches
the output. Where you edited a carve-out sentence, also confirm its hedge,
quantifier, negation, or conditional word is unchanged word for word and
still governs the same span. The carve-outs bound what may change in the
highest-risk sentences; this check confirms nothing else drifted.
