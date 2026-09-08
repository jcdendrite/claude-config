# A hook gate replaces the no-op-dispatch CLAUDE.md rule

*2026-09-08.*

Two advisory-only fixes for a subagent dispatched solely to wait, occupy the turn, or report back immediately have both been shown to fail: a skill-body prose rule, then that rule's promotion into always-loaded `claude/.claude/CLAUDE.md`. At least two further occurrences happened after the CLAUDE.md rule shipped — this is a floor from a keyword-based transcript search, not an exhaustive count, since transcript retention rotates and two of the machine's declared roots were unreadable during the sweep. One of the post-fix occurrences had the model quoting the CLAUDE.md rule's own wording back at itself in a self-diagnosis, after already having violated it — the rule was in context and understood, not merely unloaded. [no-op-dispatch-guard.md](no-op-dispatch-guard.md) already named this exact lever ("a `PreToolUse` hook on `Agent`, accepting prompt-content matching and its false-positive cost") as the next step if the behavior recurred.

**Why [§1](hook-enforced-gates.md) now applies on its own terms.** That entry's whole argument was that no mechanical predicate existed: the `PreToolUse` `Agent`/`Task` payload carries no non-content signal for "this dispatch does no work," and matching on "another dispatch is already in flight" would deny legitimate parallel fan-out. A bounded predicate now exists — see below — so [§1](hook-enforced-gates.md)'s hook preference applies rather than needing reconciliation.

**The conjunction that bounds the false-positive surface.** `claude/.claude/hooks/deny-no-op-dispatch.sh` denies a dispatch only when **both** hold: the prompt is shorter than `NOOP_MAX_PROMPT_LEN`, and the prompt matches a closed set of no-work idioms. A prompt at or above the ceiling is allowed regardless of content, so the gate is structurally unable to reach a prompt long enough to specify real work — including the `code-writer` dispatch that implements this hook and the `plan-architect` consult about it, both far over the ceiling. The ceiling is set well above the longest confirmed no-op prompt (a 302-character incident prompt) and well under a structured multi-paragraph task prompt.

**Both idiom arms read `prompt` only, never `description`.** A description is a display label, not an instruction surface, and nothing bounds its length the way the ceiling bounds the prompt — the one confirmed corpus occurrence carrying a description-only tell has `prompt="noop"`, already caught by the anchored stub arm without reading `description` at all.

**The two-source grounding rule for the idiom list.** Every idiom traces to one of exactly two sources, cited in the hook's own header: a phrase appearing verbatim in a confirmed no-op dispatch from the corpus, or a phrase `claude/.claude/CLAUDE.md`'s Agent Briefing bullet itself names as a prohibited shape (`occupy the turn`, `hold while other dispatches finish`) — a live source, since the incident session quoted that bullet at itself. A closed, sourced list is what keeps this from becoming the open-ended phrase-upkeep problem a prior plan's hook rejection warned against. A speculative addition grounded in neither source does not go in the list. A grounded source is necessary but not sufficient: a read-scoping instruction ("do not read any files") restricts tool access rather than stating "do no work," a materially different instruction shape from every idiom actually in the list, so it stays excluded regardless of its own corpus provenance.

**No `subagent_type` filter.** The confirmed post-fix occurrences used two different `subagent_type` values (`fork`, `general-purpose`), and one pre-fix occurrence recorded no `subagent_type` field at all, so a reviewer-persona-style self-filter (as `require-architect-consult.sh` uses) would have missed most of the corpus. Every `Agent`/`Task` dispatch is evaluated, matching CLAUDE.md's own "an agent — of any type" wording.

**No disable sentinel.** The escape here is the rephrase: a legitimate dispatch that trips this gate has real work to describe, and describing it clears the gate while also fixing the prompt. `require-architect-consult.sh`'s sentinel exists because its compliance path costs a full Opus consult. This one costs a sentence.

**Known gaps**, stated rather than left implicit:

- This gate implements the observed corpus shapes, not the whole CLAUDE.md rule. A differently-worded future no-op prompt using none of the listed idioms is not caught, and a no-op instruction padded above the ceiling is not caught. The CLAUDE.md bullet remains the primary surface. This hook closes the recurring shapes beneath it.
- `placeholder` matches only in the anchored whole-prompt arm, never as a free substring, so a short legitimate prompt like "Replace the placeholder on line 12" is not denied.
- A no-op dispatch whose only tell lives in `description`, paired with a `prompt` that is neither a stub token nor an idiom match, is not caught: neither arm reads `description`.
- Post-ship false-positive drift on this gate has no standing signal — no counter, no allow-path near-miss recorder, matching every other always-on `deny-*` gate in this repo. Unlike those siblings, this gate matches free-text prose idioms against live session-generated content, so its false-positive rate is more exposed to future drift. A future check of this gate's actual deny rate is a deliberate, manual transcript re-sweep, not an automatic one.
- This gate is a cooperative guardrail, not an adversarial-resistant security boundary. A caller can clear it by padding a no-op prompt past the ceiling or avoiding the closed idiom list, so it must not be cited as evidence of an enforced cost or abuse control.
- `do(ing|es)? nothing` and `no action` can describe another actor's inaction rather than the dispatched agent's own, e.g. "Check whether the retry handler does nothing on the third attempt." This is an accepted false-positive residual: narrowing either idiom further risks losing real no-op coverage.
- `tr -s '[:space:]'` and the hook's own single-space trim operate on ASCII whitespace only, so a no-op idiom padded or separated with non-ASCII whitespace (e.g. U+00A0 NBSP) passes through uncollapsed and evades both regex arms, not only the anchored stub-token arm. This is empirically observed on glibc (`C`, `C.UTF-8`, `en_US.UTF-8`), not spec-derived from a cited `tr` standard, and is unverified on BSD/macOS `tr`.

## Sources

- `claude/.claude/hooks/deny-no-op-dispatch.sh` — the gate itself.
- `claude/.claude/hooks/tests/test_deny_no_op_dispatch.py` — the fixture set pinning the ceiling and the idiom list.
- `claude/.claude/CLAUDE.md` §Agent Briefing — the rule's text, unchanged by this decision. Only its enforcement changes.
- [no-op-dispatch-guard.md](no-op-dispatch-guard.md) — the prior decision this supersedes, including its "Revisit" clause naming this exact lever.
- [hook-enforced-gates.md](hook-enforced-gates.md) — the general preference this decision now applies on its own terms.
- `claude/.claude/hooks/require-architect-consult.sh` — the sibling `Agent`/`Task`-matching gate this hook's bootstrap and deny-message shape are modeled on.
