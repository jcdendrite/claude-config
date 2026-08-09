# Hashline edit format: measured, declined, and why

*Part of the [claude-config case studies](../case-studies.md).*

**The problem.** The [Stencil harness post](https://stencil.so/blog/the-harness-problem) reports large coding-benchmark gains from replacing a str_replace-style edit tool with "hashline" — content-hashed line tags (`2:f1|  return "world";`) the model references instead of reproducing exact old text. The headline is "+15pts avg over patch, 16 models." That headline measures against `patch` (Codex's `apply_patch` format); Claude Code's built-in `Edit` tool uses `str_replace`, a different mechanism the article also benchmarks separately.

**Question.** Should this repo replace Claude Code's built-in `Edit`/`Write` with an MCP-based hashline implementation?

**Short answer.** No. Three things make the headline inapplicable to this harness, and one real defect the measurement surfaced is being left unpatched on purpose:

1. The relevant column is Δ REPLACE, not Δ PATCH — the article's own separate benchmark for str_replace-style tools. Sonnet 4.5 there is **+3.3**, not +15. Opus is not benchmarked at all, and this machine runs `"model": "opusplan"`.
2. Hashline's second claimed benefit — rejecting edits against a stale file — is already implemented. The live `Edit` tool description reads *"You must Read the file in this conversation before editing, or the call will fail,"* and `Read`: *"the harness tracks file state for you."*
3. Measured against this machine's own transcript corpus (below), str_replace-mechanical `Edit` failures are **0.77%** of `Edit` calls, and the anchoring-token overhead hashline would eliminate is **0.67%** of total assistant output tokens. Both are far below the level that would justify the mechanism swap described in "Why not implement hashline."

The one real defect the measurement surfaced — this repo's own credential-redaction hook occasionally corrupting an `old_string` it wasn't the intended target of — is recorded below and deliberately left unpatched: the trade favors the credential backstop.

## How this was measured

The `edit-format` subcommand of `claude/.claude/scripts/transcript-analysis.py` does one single pass over the transcript corpus, producing every figure below from one scan rather than several ad hoc scripts run at different moments — a corpus that grows every session cannot otherwise guarantee two runs agree.

Command (redacted form; the real invocation repeats `--config-dir` once per non-default Claude Code account profile under `~/.config/claude-accounts/`, never named literally here or in the tool's own output):

```
transcript-analysis.py edit-format --config-dir <profile-1> --config-dir <profile-2> --config-dir <profile-3>
```

Snapshot taken 2026-08-08 across four config dirs — the default `~/.claude/projects` plus three additional account profiles. Honest limits:

- **Point-in-time.** The transcript store is mutable and grows every session, including the one that produced this snapshot. The figures are a snapshot: date, corpus size, and the command above. Re-running later will not reproduce these exact counts — only the classification *behavior* is guaranteed reproducible, via `TestEditFormat`'s synthetic fixtures in `claude/.claude/scripts/tests/test_transcript_analysis.py`.
- **Undercounts depth-5 workflow-agent transcripts.** `edit-format` reuses this file's shared `_read_session_file`, which merges a session's direct `subagents/*.jsonl` children but not a further-nested `subagents/workflows/wf_*/agent-*.jsonl` shape some sessions also carry. Every other subcommand in this file has the same gap; widening the shared glob is out of scope here since it would silently change already-shipped output for every other subcommand too.
- **`MultiEdit` returned 0.** The tool no longer exists in this harness. It stays in the subcommand's own recognized-tool set so a future reintroduction is counted against its own denominator rather than silently dropped.
- **Governance-hook denials are excluded from the failure-rate figures below.** They reflect this repo's own review pipeline (worktree enforcement, plan-review gate, reviewer-tree-mutation guard, and similar), not the edit format — see "What actually blocks edits in this corpus."

## The numbers

**7,526 `Edit` + 2,745 `Write` calls**, all four config dirs combined.

| Tool | Failure | count | % of that tool's calls | Fixed by hashline? |
|---|---|---|---|---|
| `Write` | `file has not been read yet` | 141 | 5.14% | **No** — hashline needs a prior read too |
| `Edit` | `String to replace not found` | 48 | 0.64% | Partly — see cause attribution below |
| `Edit` | `file has not been read yet` | 6 | 0.08% | **No** |
| `Edit` | multiple-match rejection | 4 | 0.05% | Yes |
| `Edit` | `old_string`/`new_string` identical (no-op) | 6 | 0.08% | No — not a format defect |

**str_replace-mechanical totals 58 of 7,526 (0.77%)** — `not_found` + `unread` + multi-match (48+6+4), deliberately excluding the 6 no-ops: a no-op is the model asking for a change that does nothing, which succeeds or fails identically regardless of edit format. Including no-ops gives **64 of 7,526 (0.85%)** as every non-governance `Edit`-tool error; cite 0.85% wherever the claim is "every `Edit` error, full stop," and 0.77% wherever the claim is specifically about str_replace's anchoring mechanism.

### `not_found` cause attribution

Each `not_found` failure is attributed by pairing it with the **next `Edit` on the same file_path** and diffing the two `old_string`s under whitespace normalization — not by pattern-matching the failed string in isolation. An earlier attempt at this measurement used a regex (`\t| {2,}\S`) applied to the failed `old_string` alone; it fired on 60.1% of *successful* `Edit` calls too, a 3.5-point gap from its failure-side rate, meaning it detected "this string contains indentation" (true of most code), not "this edit failed because of whitespace." A 12x-over-attributing classifier is exactly the failure mode the `edit-format` subcommand's own test suite now guards against with a negative fixture (an indented retry whose *content* differs still lands in `content_differs`, never `whitespace_only`).

| cause | count | share |
|---|---|---|
| content genuinely differs (changed or misremembered) | 36 | 75.0% |
| `[REDACTED-CREDENTIAL]` placeholder from this repo's own hook | 6 | 12.5% |
| abandoned, no retry | 3 | 6.2% |
| **whitespace-only difference — the genuine hashline target** | **2** | **4.2%** |
| identical retry | 1 | 2.1% |

**Ceiling on benefit: 2 whitespace cases + 4 multi-match = 6 of 7,526 `Edit` calls = 0.08%.** The whitespace slice alone is 0.03%.

### Anchoring token cost

Failure rate is not the main cost of `str_replace`. To guarantee a unique match the model emits enough surrounding context on **every successful edit**, not just failed ones:

| | chars | ~tokens @4 c/t | |
|---|---|---|---|
| `old_string` — pure anchoring | 4,039,669 | ~1,009,917 | **32.8% of Edit payload** |
| `new_string` — the actual change | 8,271,221 | ~2,067,805 | |
| `Write` content | 20,548,784 | ~5,137,196 | hashline does not reduce this |
| all assistant output, all sessions | — | **151,825,297** | |

Mean `old_string` is **537 chars per edit**; 21.7% of edits carry one over 700 chars. `old_string` is **0.67%** of total assistant output tokens — a floor, not a ceiling: `new_string` re-emits most of the same surrounding context under `str_replace`'s "reproduce the whole edited region" convention, so the addressable ceiling is closer to `old_string` + `new_string` combined — **2.03%** of total output tokens. Even at that upper bound, the article's headline "−24% output tokens" does not transfer, for two reasons visible in the article's own text: its savings are failure-driven (*"Grok 4 Fast's output tokens dropped 61% because it stopped burning tokens on retry loops"* — at a 0.77% failure rate here, there are almost no retry loops to eliminate), and its denominator is a pure-editing benchmark where `Edit` calls are essentially all the output, unlike a mixed session where `Edit`/`Write` payload is a small slice of what gets emitted.

### What actually blocks edits in this corpus

Re-bucketing every error paired to an `Edit`/`Write`/`MultiEdit` call by this repo's own six governance-hook/harness denial patterns:

| gate | count |
|---|---|
| worktree-enforcement | 78 |
| plan-review gate | 69 |
| reviewer-tree-mutation | 59 |
| worktree-isolation | 23 |
| path-spelling | 17 |
| permissions | 16 |
| **TOTAL** | **262** |

Self-imposed governance blocks roughly **4.5x** more edits (262) than str_replace-mechanical failures do (58). That is not an argument against the hooks — each denial is the gate doing its job — but it locates where edit friction in this setup actually comes from, and it is not the edit format.

A residual 33 edit-family errors match neither the four known str_replace failure shapes nor the six governance patterns above — reported as an explicit `unclassified` count rather than silently dropped. Sampling them (not reproduced here, since several embed other private projects' file paths) shows a mix of user-rejected tool calls, missing-required-parameter validation errors, the harness's own auto-mode permission classifier, and other projects' own PreToolUse hooks (a migration-filename gate, a config-file protection gate) — none of them str_replace-format failures, all of them outside this repo's own scope.

### Per-account breakdown

Emitted by `edit-format` itself through the same `account-N` labeling `_build_redact_map` already establishes for this file's other multi-account reports — never the raw config-dir path or account name:

```
  account-1  calls= 5,952  unread=   5  not_found=  44  multi=  3  addressable=0.8%
  account-2  calls=    59  unread=   0  not_found=   0  multi=  0  addressable=0.0%
  account-3  calls=   611  unread=   0  not_found=   3  multi=  0  addressable=0.5%
  account-4  calls=   904  unread=   1  not_found=   1  multi=  1  addressable=0.2%
```

The rate is not uniform across accounts, but every account's own rate is well under the 3% revisit threshold below.

## Why not implement hashline

Full adoption is mechanically possible and disproportionate to a 0.08% ceiling. The mechanism would have to be: ship an MCP server exposing hashline `read`/`edit`/`write`, then remove the built-ins so the model actually uses it instead of the tool the system prompt keeps steering it toward. Both halves are forced by the harness:

- MCP tools are hard-prefixed — *"The full form is `mcp__plugin_<plugin-name>_<server-name>__<tool-name>`"* — so an MCP tool can never be named `Edit`; it would coexist with the built-in, not replace it.
- Removing the built-in requires a bare-name deny — *"A bare tool name like `Bash` removes the tool from Claude's context entirely, so Claude never sees it"* (Claude Code permissions docs). Scoped rules like `Edit(path)` only block individual calls.

That combination breaks real things that ship to every stow consumer of this repo, not just this project:

1. **Credential-value redaction and three read guards go blind.** `deny-env-reads.sh`, `deny-credential-file-reads.sh`, and `deny-data-file-reads.sh` are registered on the `Read` matcher; so is `redact-credential-values.sh`. None match `mcp__hashline__read`. Removing the built-in `Read` opens exactly the credential residual the next section declines to open — and loses `deny-data-file-reads.sh`'s size cap along with it.
2. **Roughly a dozen hook registrations lose their matcher**, two of them integrity gates, not conveniences (`enforce-marker-script-shape` guards the review-marker state this repo's own CLAUDE.md treats as forge-resistant).
3. **`Read` loses non-text capability** (PDF page ranges, images, Jupyter notebooks with outputs) that a line-oriented hashline reader cannot carry.

Two lighter primitives were considered and set aside:

- **A `PostToolUse` `Read` hook that tags every line with a hash, built-ins otherwise untouched** — this would be actively harmful, not just insufficient. The model would quote the tagged text back in `old_string` and every edit would miss. This isn't speculative: it's precisely the mechanism behind the 6 measured `[REDACTED-CREDENTIAL]` failures below, just at near-total line coverage instead of the redaction hook's much narrower footprint.
- **A `PreToolUse` `Edit` hook that silently re-anchors a whitespace-only mismatch to the file's real text.** Once cause attribution is corrected (above), this targets exactly **2 measured cases**, against a new always-on mutation layer on the edit path where a wrong re-anchor would silently edit the wrong region. Recorded here as the option to revisit if the rate ever moves — not adopted now.

## The redaction defect — recorded, not patched

`redact-credential-values.sh` rewrites credential-shaped values (GitHub token prefixes, PEM blocks) to `[REDACTED-CREDENTIAL]` in `Bash`, `Read`, `WebFetch`, `Grep`, and `Task` tool output, as a backstop for credentials that reach context through a path the file-read-specific deny hooks don't cover. When a matched value sits inside a region the model later tries to `Edit`, the redacted placeholder no longer matches the file's real on-disk text, and the edit fails with `String to replace not found` — 6 of the 48 `not_found` failures measured above, 12.5% of that bucket.

Two candidate fixes were considered and both rejected:

- **Skip mutation on `Read` tool output specifically.** The hook's own header describes itself as covering channels the file-read-specific gates don't (`WebFetch` body, `Grep` match, subagent output) — `Read` isn't named there, and `Read` already has `deny-data-file-reads.sh`'s size cap. That reading is close, but it isn't quite what the header says: the parenthetical names the *motivating* channels, not the full matcher — `Bash` and `Read` are both in the matcher and both absent from the list, and the header now says so explicitly to foreclose the same misreading. Removing `Read` from the matcher opens a real residual: a credential-shaped value hardcoded in a **non-credential-path** file (a token literal in an application source file) would reach context unredacted, since the path-based gates never fire on it. That buys back 6 failures in 7,526 `Edit` calls (0.08%) — self-correcting, since the model re-reads and retries — against an unrecoverable credential leak. The asymmetry does not favor the trade.
- **A `PostToolUse` `Edit` hook that explains the failure when a denied `old_string` contains `[REDACTED-CREDENTIAL]`.** This is a layer whose only purpose is narrating a failure the *previous* layer creates — compounding defensive layers instead of questioning the foundation. At 0.08% of `Edit` calls, that doesn't earn a permanent hook on the edit path in every stow consumer's session.

The accepted disposition: no code change to `redact-credential-values.sh`'s redaction behavior or matcher. This is a measured, accepted cost, not an oversight.

## Decision

Decline hashline. Measured against this harness and this machine's own transcript corpus, the mechanism swap buys back at most a 0.08% `Edit`-failure ceiling and a 0.67%–2.03% token-overhead ceiling, at the cost of a security-relevant migration (re-keying every `Read`-matched hook) and an integrity regression (losing two forge-resistance gates) to remove the built-in tools. The gap between the article's headline and what applies here is explained entirely by primary sources: a different comparison column (Δ REPLACE vs. Δ PATCH), a benefit already implemented (read-before-edit), and a failure-driven savings mechanism that has almost nothing to eliminate at this corpus's actual failure rate.

**Numeric revisit trigger.** Reconsider if any of the following becomes true:

- str_replace-mechanical `Edit` failures exceed **~3% of `Edit` calls** (4x today's measured 0.77%), re-derived via `transcript-analysis.py edit-format`.
- `old_string` overhead exceeds **~2.7% of total assistant output tokens** (4x today's measured 0.67%), same subcommand.
- A Claude model ships with a benchmarked Δ REPLACE gain materially above Sonnet 4.5's **+3.3** in a comparable published benchmark.

## Sources

- **[Stencil harness post](https://stencil.so/blog/the-harness-problem)** — the hashline proposal, the Δ PATCH/Δ REPLACE benchmark table, and the failure-driven-savings and pure-editing-benchmark quotes cited above.
- **`claude/.claude/scripts/transcript-analysis.py`** — the `edit-format` subcommand: per-tool call/failure census, governance-hook re-bucketing, `not_found` cause attribution, and token-overhead accounting, all from one pass over the transcript corpus.
- **`claude/.claude/scripts/tests/test_transcript_analysis.py`** — `TestEditFormat`: the classifier-honesty negative test (indentation alone must not fire the whitespace-cause bucket), per-tool denominator separation, the explicit unpaired/unclassified counters, and one fixture per governance pattern.
- **`claude/.claude/hooks/redact-credential-values.sh`** — the credential-value redactor cited above; its behavior and matcher are unchanged, its header comment corrected (motivating channels vs. full matcher).
- **Live `Edit`/`Read` tool descriptions** (this session) — the read-before-edit guarantee already in place.
- **[Claude Code permissions docs](https://code.claude.com/docs/en/permissions)** and **[MCP docs](https://code.claude.com/docs/en/mcp)** — bare-name tool denial and MCP tool name prefixing.
