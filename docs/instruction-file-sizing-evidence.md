# Instruction-file sizing: what the evidence actually supports

A register of every primary source found for how large, dense, or
instruction-heavy an always-loaded agent-instruction file (CLAUDE.md,
AGENTS.md, and comparable) can be before it costs adherence — assembled to
ground `claude/.claude/CLAUDE.md`'s size/density ratchet and the
`ai-instruction-and-memory-files` skill's length guidance, rather than let
either restate an unsourced number. Sibling to
[`design-decisions.md`](design-decisions.md) and
[`case-studies.md`](case-studies.md): this page is a sourced evidence
register, not a decision record or an empirical study of this repo's own
corpus.

Quotes and figures below were fetched from the cited primary sources by the
session assembling this register; this document transcribes them rather than
re-fetching each URL independently. Tier matters. A peer-reviewed venue
outweighs an unreviewed preprint. A vendor's own asserted recommendation
is not the same claim as a measured result, even when both use the word
"degrades." Every entry below states its tier explicitly; do not upgrade one
in a later edit without a new source to justify it.

## 1. Vendor guidance — first-party, all asserted, none cite measurement

Every vendor below tells developers to keep instruction files short and
well-structured. None of the four publishes the study that produced the
number — each is a documented recommendation, not a measured result. Treat
every figure in this section as **asserted, not measured**.

### Anthropic

- **Claude Code — How Claude remembers your project** (`code.claude.com/docs/en/memory`), official vendor docs:

  > "**Size**: target under 200 lines per CLAUDE.md file. Longer files
  > consume more context and reduce adherence."

  > "**Structure**: use markdown headers and bullets to group related
  > instructions. Claude scans structure the same way readers do: organized
  > sections are easier to follow than dense paragraphs."

  The 200-line target is scoped to CLAUDE.md specifically: "This limit
  applies only to `MEMORY.md`. Claude Code loads a CLAUDE.md file of up to
  4 MiB in full and skips a larger file. Shorter files produce better
  adherence." The 4 MiB figure is a hard skip threshold, not a
  recommendation — a file under it loads in full regardless of size, and a
  file over it is silently dropped.

  The same page states `MEMORY.md`'s load window: "The first 200 lines of
  `MEMORY.md`, or the first 25KB, whichever comes first, are loaded at the
  start of every conversation." This 25KB figure is Anthropic's stated
  `MEMORY.md` load window, extrapolated to CLAUDE.md as the nearest
  in-family precedent — Anthropic states no byte/KB figure for CLAUDE.md
  itself. 25KB over 200 lines implies roughly 128 bytes per line at that
  boundary.

- **Effective context engineering for AI agents** (`anthropic.com/engineering/effective-context-engineering-for-ai-agents`), official vendor engineering blog:

  > "These factors create a performance gradient rather than a hard cliff:
  > models remain highly capable at longer contexts but may show reduced
  > precision for information retrieval and long-range reasoning."

  > "LLMs have an 'attention budget'..."

  > "Context, therefore, must be treated as a finite resource with
  > diminishing marginal returns."

  This is a general context-engineering statement about long-context
  behavior, not an instruction-file-specific claim, and cites no benchmark
  numbers of its own.

- **Anthropic prompt-engineering docs**, official vendor docs:

  > "Queries at the end can improve response quality by up to 30 percent in
  > tests, especially with complex, multidocument inputs."

  Scoped explicitly to long-document retrieval tasks. "In tests" is the
  entire sourcing given — no methodology, sample size, or model version is
  published alongside the figure. Do not generalize this number to
  instruction-file placement without that same scope.

### OpenAI

- **GPT-4.1 prompting guide**, official vendor docs:

  > "if you have long context in your prompt, ideally place your
  > instructions at both the beginning and end of the provided context, as
  > we found this to perform better than only above or below."

  First-party and asserted-as-tested ("we found"), but no methodology,
  benchmark, or numeric result is published alongside the claim.

### Google

- **Gemini API — Prompting strategies** (`ai.google.dev/gemini-api/docs/prompting-strategies`), official vendor docs:

  > "Break down instructions: Instead of having many instructions in one
  > prompt, create one prompt per instruction."

  No benchmark or numeric threshold cited.

### Cross-vendor size table

| Vendor / product | Figure | Kind |
|---|---|---|
| Anthropic — CLAUDE.md | Under 200 lines | Recommendation |
| Anthropic — CLAUDE.md | 4 MiB | Hard skip threshold (file silently ignored past this size) |
| Anthropic — MEMORY.md | 200 lines or 25KB, whichever comes first | Hard load window |
| OpenAI Codex — `project_doc_max_bytes` | 32 KiB (default) | Hard truncation |
| GitHub Copilot | "No longer than 2 pages" | Recommendation |
| GitHub Copilot | 4,000-character code-review instruction cutoff | Removed 2026-06-12 (no longer in force) |
| Google Antigravity | 12,000 characters per rules file | Hard limit |
| Cursor | "Keep rules under 500 lines" | Recommendation — per-file, in an architecture of many small rule files; not comparable to one aggregate instruction file |
| Google Gemini CLI | — | No size guidance published |
| Google Jules | — | No size guidance published |

**No vendor publishes a token-denominated budget.** Every non-line figure
above is stated in bytes, KiB, or characters — never tokens. A
bytes-to-tokens conversion (via an assumed bytes-per-token ratio, however
derived) would introduce tokenizer-specific variance with no vendor figure
on the other side to validate it against, for no benefit over citing the
byte figure a vendor already publishes directly.

## 2. Instruction-count degradation — peer-reviewed, the best-grounded dimension

Unlike vendor guidance, this dimension has multiple peer-reviewed
publications with a consistent direction: measured task performance falls
as the number of instructions in a single prompt rises. This is the
strongest-evidenced dimension in this register.

- **FollowBench** (Jiang et al., **ACL 2024**, peer-reviewed):

  > "the performance typically diminishes as we progress from L1 to L5 for
  > almost all models."

  Reported GPT-4-Preview-1106 Hard Satisfaction Rate by instruction-count
  level: L1 84.7 → L2 75.6 → L3 70.8 → L4 73.9 → L5 61.9.

- **ComplexBench** (Wen et al., **NeurIPS 2024 Datasets & Benchmarks track**, peer-reviewed):

  > "as the complexity of composition types within instruction increases,
  > the performance of all LLMs significantly drops, especially on
  > Selection and Chain."

- **"When Instructions Multiply"** (Harada et al., **EMNLP 2025 Findings**, peer-reviewed):

  > "Our experiments with the created benchmarks across ten LLMs reveal that
  > performance consistently degrades as the number of instructions
  > increases."

  The two benchmarks introduced top out at 10 instructions (ManyIFEval) and
  6 instructions (StyleMBPP) — the measured range does not extend to
  dozens or hundreds of instructions.

- **SIFo** (Chen et al., **EMNLP 2024 Findings**, peer-reviewed):

  > "all models show a monotonic decline in performance as the position of
  > an instruction in a sequence increases."

  This measures instruction *position within a sequence*, not instruction
  count alone. SIFo's design conflates the two: a later instruction
  typically depends on correctly following an earlier one, so degradation
  could reflect cumulative dependency difficulty rather than count.

- **InfoBench** (Qin et al., **ACL 2024**, peer-reviewed) and **CELLO** (He et al., **AAAI 2024**, peer-reviewed) report the same direction (more instructions, lower adherence). Exact per-level tables were not retrieved for this register — noted as a gap rather than presented as data.

- **IFScale** (Jaroslawicz et al., arXiv 2507.11538) — **preprint, not peer-reviewed.** Label it as such wherever cited. This is the only source in this register with a quantified density curve at high instruction counts:

  | Model | 10 instructions | 100 instructions | 500 instructions |
  |---|---|---|---|
  | Claude 3.7 Sonnet | 100% | 94.8% | 52.7% |
  | Gemini 2.5 Pro | 100% | 98.4% | 68.9% |
  | GPT-4o | 94% | 49% | 15.4% |

  The task is narrow — keyword inclusion in a generated business report, one
  instruction type repeated many times. These numbers are **not
  transferable** to a general instruction-file adherence estimate; do not
  cite them as "a model follows N% of instructions at M count" outside that
  task.

- **IFEval** (arXiv 2311.07911) — the field's most-used instruction-following benchmark, and itself **not peer-reviewed** despite that ubiquity. Worth recording precisely because so much downstream work (including some cited above) builds on it.

## 3. Length degradation

- **"Lost in the Middle"** (Liu et al., **TACL 2024, vol. 12, pp. 157–173**, peer-reviewed journal):

  > "performance is often highest when relevant information occurs at the
  > beginning or end of the input context, and significantly degrades when
  > models must access relevant information in the middle of long
  > contexts."

  **Critical scope note.** The paper's tasks are multi-document question
  answering and key-value retrieval only. It contains **no instruction-
  adherence task of any kind.** Citing this paper as evidence about where to
  place a *behavioral rule* in a system prompt is an extrapolation the paper
  itself does not make — it is a retrieval-position finding, not an
  instruction-following finding. See §4 below.

- **NoLiMa** (Modarressi et al., **ICML 2025, PMLR v267**, peer-reviewed):

  > "At 32K, for instance, 11 models drop below 50% of their strong
  > short-length baselines. Even GPT-4o... experiences a reduction from an
  > almost-perfect baseline of 99.3% to 69.7%."

- **"Context Rot"** (Hong, Troynikov, Huber; Chroma, 2025) — **company technical report, not peer-reviewed.** Chroma sells RAG infrastructure, a direct commercial interest in "long context degrades, use retrieval instead" as a conclusion; weigh accordingly. It does test 18 third-party models, and its controlled ablation is worth recording on its own terms:

  > "By holding the needle-question pair fixed and varying only the amount
  > of irrelevant content, we isolate input size as the primary factor in
  > performance decline."

## 4. Two claims this document exists to debunk

1. **The "30–50% compliance loss for instructions in the middle" figure has
   no primary source.** It traces to a blog post (tianpan.co, April 2026)
   that states the figure with no citation, footnote, or attribution to any
   study. It appears nowhere in this register's peer-reviewed or first-party
   sources. **Do not cite it.**
2. **"Lost in the Middle" does not license an instruction-placement claim.**
   Per the critical scope note in §3: the paper measures retrieval-task
   accuracy over document position, not rule adherence over instruction
   position. A rule in a system prompt and a fact in a retrieved document
   are not shown equivalent by this paper, or by any other source in this
   register.

## 5. The documented negative result

No peer-reviewed study and no vendor first-party publication measures
whether a model keeps following a system prompt's *N* behavioral rules as
the surrounding context grows around them — the specific question an
always-loaded instruction file's size and density raise. The two nearest
sources are unaffiliated, unpublished arXiv preprints (2607.19257,
2608.02639); this document does **not** rely on either.

**Consequence:** no numeric threshold for instruction-file size or rule
count is derivable from the current literature. §1's vendor line-count and
byte figures are real, sourced constraints worth respecting. §2's
instruction-count curves are real, sourced degradation evidence. Neither
supplies a number that says "N rules is the line, and N+1 is not."
