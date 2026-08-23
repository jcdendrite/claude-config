# tighten-prose — References

Edit-time reference for `SKILL.md`. Not loaded at runtime — read it manually
when changing a rule, to check whether the source still supports it.

## Primary source: Google Developer Documentation Style Guide

Licensed CC BY 4.0 — confirmed on the pages cited below, each carrying the
footer: *"Except as otherwise noted, the content of this page is licensed
under the Creative Commons Attribution 4.0 License, and code samples are
licensed under the Apache 2.0 License."*

**Active voice** (`developers.google.com/style/voice`):

> In general, use active voice (in which the grammatical subject of the
> sentence is the person or thing performing the action) instead of passive
> voice (in which the grammatical subject of the sentence is the person or
> thing being acted upon), although there are exceptions. Make clear who's
> performing the action.

Exceptions the same page lists: to emphasize an object over an action ("The
file is saved"), and to de-emphasize a subject or actor. Grounds rule 4's
"except when the actor is unknown or irrelevant to the reader."

**Second person** (`developers.google.com/style/person`):

> In general, address the reader of your documents using the second person
> instead of the first person: use *you* or *your* instead of *we*, *our*,
> or *us*. ... If you're telling the reader to do something, then use the
> imperative (the *you* is implied).

Informed the general register this skill rewrites toward, though the
drafted rule list does not carry a standalone second-person rule — `SKILL.md`
rule 2 (fact preservation) constrains any pronoun-scope rewrite from
changing who a sentence addresses.

This audience distinction matters because Anthropic's own guidance points
the other way for a skill or agent body specifically. The Agent Skills
best-practices page (`platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices`,
"Writing effective descriptions") requires third person, but scopes that
rule to the `description` frontmatter field — the stated reason is
discovery reliability once descriptions are concatenated into the system
prompt, not body-text readability:

> Always write in third person. The description is injected into the
> system prompt, and inconsistent point-of-view can cause discovery
> problems.

The same page's body examples are second-person imperative throughout
("Copy this checklist and track your progress"), matching this repo's own
`skill-review`/`agent-review` rule that skill and agent bodies use
imperative second person. No Anthropic source found (checked the
prompt-engineering best-practices reference, the Claude Code skills docs,
and "Writing tools for agents") recommends against second person for
prose a human reads — the constraint is scoped to that one frontmatter
field, not to agent-directed content generally. `SKILL.md` §1 keeps
skill/agent bodies in the second-person voice they're reviewed against for
exactly this reason.

**Conditions before instructions** (`developers.google.com/style/sentence-structure`):

> If you want to tell the reader to do something, try to mention the
> circumstance, conditions, or goal before you provide the instruction.
> Mentioning the circumstance first lets the reader skip the instruction if
> it doesn't apply.

Grounds the carved-out treatment of conditional clauses in rule 3: a
rewrite that reorders or splits a conditional risks breaking the very
binding this guidance protects, which is why rule 3 excludes conditionals
from rewriting entirely rather than trusting a rewrite to preserve them.

**Word list / controlled vocabulary** (`developers.google.com/style/word-list`):

> This word list covers style and usage guidelines that are specific to
> developer documentation. ... If there are multiple spellings ... use the
> first form listed, which is the most common spelling.

Grounds rule 4's "pick one term per concept and keep it for the whole
document" — a single-source-of-truth-for-terminology approach, the same
directional argument as the word list's own consistency rationale.

## Corroborating source: Digital.gov Plain Language guidelines

`digital.gov/guides/plain-language/principles` — no explicit reuse-terms
statement found on the page (checked directly), so it corroborates the same
rule directions rather than serving as a licensing anchor for quoted text.

> Have a topic sentence. Good opening sentences help organize the structure
> of writing. Use the active voice. Active voice helps the message stay
> clear and easy-to-read. ... write for your audience.

Corroborates active voice and audience-appropriate language as independent
directional support, not as the source the skill body cites by name.

## Lineage note: ASD-STE100

The user's original ask named ASD-STE100 (Simplified Technical English)
specifically. Checked directly against `asd-ste100.org`'s FAQ and About
pages:

> ASD-STE100 is fully owned by ASD, Brussels, Belgium.

> The standard is available to everyone free of charge. The only file
> format for distribution is PDF.

STE has 53 writing rules and a controlled dictionary of approximately 900
approved words (About page, FAQ page). Free to obtain, but neither page
states reproduction or reuse terms for the rule text or dictionary — no
license grant, no public-domain declaration. A public repo can't safely
quote or paraphrase the standard's rule text at the level this repo's
grounding requirement calls for, which is why the skill is grounded on the
openly-licensed Google source above instead and does not claim ASD-STE100
certification or compliance. The concept — a controlled technical-language
category — traces to STE; the implementation does not.
