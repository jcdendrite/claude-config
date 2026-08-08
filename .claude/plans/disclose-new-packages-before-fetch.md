# Disclose new third-party packages before they are fetched

## Context

**Goal: when an agent's action will pull a third-party package that is not already declared, the human must be told which package, which version, and why — before approving it.**

Three surfaces, and only one is genuinely closed today:

1. **Named install commands** (`npm install <pkg>`, `pip install <pkg>`, `uv add`, `pnpm dlx`, `npx -y`) — hard-denied at `deny-network-installs.sh:206-229`, plus 14 install literals in `permissions.deny`. Nothing unnamed gets fetched here. But the deny text tells the agent to hand the command over via the `!` escape **without requiring it to say what the package is or why**, so the handoff is uninformative. That hook's own header also documents four accepted false-allows (path-prefixed manager, bare `npx`, `pip install -e <VCS-URL>`, and the `permissions.deny`-literal-only managers) — this surface is *mostly* closed, not fully.
2. **The manifest edit.** An agent adds entries to `package.json` and no mechanism names them. Under `acceptEdits` the write may not prompt at all.
3. **The bare restore that follows.** `_install_has_leftover_token` (`deny-network-installs.sh:63`) deliberately allows argument-less `npm install` / `pnpm install` as a lockfile restore. Run straight after a manifest edit, that fetches brand-new packages while the prompt shows only the string `pnpm install`. The hook inspects command tokens and never sees that the manifest changed underneath it.

Surfaced by a session that added four devDependencies to a manifest during unrelated work, reasoning that the packages already existed elsewhere in the same monorepo at identical versions. Nothing required it to say so first.

## Approach

Three layers with **different jobs** — this is deliberately *not* defense in depth, and must not be described as such. All three depend on the same agent cooperating; there is no deny-class backstop, by decision.

**Layer 1 — the duty (prose), agent-cooperative, not enforcement.** A `§Safety` bullet in `claude/.claude/CLAUDE.md` creating a per-instance disclosure-and-confirm duty for adding an undeclared package by any route. It is the only layer carrying *why the package is needed*, and the only one reaching surfaces 1 and 3 at all. The remedy differs by route: a **manifest edit** is disclose-then-confirm; an **install command** stays disclose-then-hand-to-`!` — `CLAUDE.md:82`'s existing prohibition is strict and confirmation does not unlock executing it directly. The bullet's "why it is needed" clause is deliberately terse and defers provenance research to the existing "New third-party dependencies" bullet under "Ground every choice" — this bullet owns *when* (before the fetch, requires confirmation), that one owns *what to research*.

**Layer 2 — the reminder (hook), best-effort.** A `PreToolUse` hook on `Edit|Write|MultiEdit` that parses added dependency names out of a `package.json` write and returns `permissionDecision: "ask"` naming them.

**Layer 3 — the handoff text.** Amend `_INSTALL_ALTERNATIVE` so the `!`-handoff demands package, version, and rationale. Note this string goes to **stderr → the model**, not to the human: it is better-placed prose that instructs the agent to disclose, not a disclosure channel itself.

### Step 0 gates Layer 2

Anthropic's docs do not specify whether a hook-returned `ask` renders under `acceptEdits`/`bypassPermissions` (verified against `https://code.claude.com/docs/en/hooks.md` this session). `docs/security-hardening.md:172-199` already recorded that position and built no enforcement on it. Layer 2 exists to cover the manifest edit *in the mode where it may not prompt* — so its own justification rests on the unverified behavior specifically in those two modes, not in `default` mode where a hook-`ask` trivially renders and proves nothing about the gap Layer 2 targets.

**The engineer runs the spike before Layer 2 is built** (see Verification Step 0, now fully specified: which modes, an isolated vehicle, and the decision rule for a split outcome). If a hook-returned `ask` does not render in `acceptEdits`, Layer 2 is dropped from this PR and **only Layers 1+3 ship** — see "Critical files, Layers-1+3-only branch" below for that branch's exact deliverable. Whatever the spike finds gets written into `docs/security-hardening.md`, dated and version-tagged, since a future Claude Code release could flip the answer with no signal — durable value for every future hook in this family, not just this one.

### Scope: `package.json` only

Restricted from the five ecosystems originally drafted. This is what kills the largest implementation hazard: `tomllib` is Python 3.11+, and stock macOS `/usr/bin/python3` is **3.9.6** with no `tomllib` — a top-level import would fail before dispatch and silently disable the hook for *every* ecosystem on those machines. JSON-only needs `json` alone, which is stdlib everywhere. It also drops the `requirements.txt` and `go.mod` grammars, each a false-positive surface (`-r` includes, `-e` editables, environment markers, PEP 503 name normalization, `replace`/`exclude` directives). Other ecosystems are a follow-up once the false-positive rate has been observed.

### Distinguishing a new dependency from a version bump

An `Edit`'s `new_string` is a JSON *fragment* that no parser accepts, and regex-scanning it misreads `scripts` and `engines` entries as packages. The helper **reconstructs the post-write manifest and diffs dependency key sets against the on-disk file**:

| Tool | Post-state | Pre-state |
|---|---|---|
| `Edit` | on-disk content, `old_string`→`new_string`, honoring `replace_all` | on-disk content |
| `MultiEdit` | edits applied sequentially **against a running buffer**, honoring each edit item's own `replace_all` field the same way the `Edit` row does | on-disk content |
| `Write` | `content` verbatim | on-disk content, or empty if absent |

`new_names = post_deps - pre_deps` over the **union** of `dependencies`, `devDependencies`, `peerDependencies`, `optionalDependencies`. Union, not per-section, so a `dependencies`→`devDependencies` move correctly does not fire. Empty set → silent.

**Pre-state parse failure** (on-disk `package.json` doesn't parse as JSON — an agent mid-repair of a broken manifest) is a named case, not left to a test comment to imply: it routes to the same degraded-`ask` disposition as an unparsable helper *output* (see "Fail-open, but loud" below) — the pre-state is agent-authored content exactly as the post-state is, so silently treating it as an empty dependency set (which would fire on every existing dependency as "new") or silently allowing (which would suppress a disclosure for a manifest that just gained a dependency) are both wrong; loud-not-silent is the same rationale either way.

### Alternatives set aside

- **`additionalContext`** — the only non-blocking PreToolUse field, but the docs state Claude Code "wraps the string in a system reminder… it doesn't appear as a chat message in the interface." Reaches the model, never the human.
- **A `paths`-scoped rule file** under `claude/.claude/rules/` — loads instruction text into model context but produces no artifact at the prompt; a second prose layer, not a channel.
- **`PostToolUse`** — fires after the write lands.
- **`deny`-class gate with marker release** — reliable in every mode, but needs a new marker family and a release mechanism for what is a transparency problem.
- **Pure bash + `jq`** — cannot reconstruct an edit.

### Assumption ledger

**Root problem:** approval of a package fetch is uninformed, because no surface names the package.

**Givens** (beyond this design's reach):
- Whether a hook-returned `ask` renders under `acceptEdits`/`bypassPermissions` is vendor-undocumented — Anthropic owns the hook contract. `[verified: code.claude.com/docs/en/hooks.md, fetched this session]`. **Resolved by the Step 0 spike this session:** it does render under both modes (Claude Code 2.1.223), recorded in `docs/security-hardening.md`. `auto` mode's classifier layer remains untested and open.
- No PreToolUse field renders text to the human without blocking the call. `[verified: same source]`

**Mechanisms:**

| Mechanism | Justification | Anchors |
|---|---|---|
| CLAUDE.md `§Safety` bullet | Only layer carrying rationale; only layer reaching all three surfaces | `root` |
| `ask`-class hook, `package.json` only | Lightest primitive reaching the human; four lighter ones enumerated and rejected above | `root` |
| Python helper | Edit reconstruction is not expressible in bash+`jq`; `json` is stdlib; `parse-git-command.py` is the precedent | `row: ask-hook` |
| `_INSTALL_ALTERNATIVE` amendment | The `!`-handoff has no prompt at all | `root` |

**Assumptions:**
- Staged scope (prose enforces; hook is `package.json`-only and best-effort); moratorium dropped; trigger = new direct dependency only; `npm pkg set` closure deferred to a follow-up rather than closed in this PR. `[engineer-verified]`
- `hook-class: informational` exempts the hook from `test_hook_alignment.py` Layer-2's deny-schema battery, since `ask-` matches no gate-mandating prefix. `[verified: test_hook_alignment.py Layer 1; ask-review-permissions.sh is informational]`
- `npm pkg set dependencies.<name>=<ver>` is allowed by `deny-network-installs.sh` today, and closing it correctly needs a token-*pair* match plus a value-prefix gate, not a single-token verb addition. `[verified: read against `_install_check_npm_family`'s verb loop, `deny-network-installs.sh:120-132`]`
- `/usr/bin/python3` is 3.9.6 without `tomllib` on stock macOS. `[verified: run this session]`
- The hook itself (not whether it renders to the human) behaves identically for a `code-writer` subagent's tool calls as for the top-level session — resolved by a direct pytest case (see Critical files), not left as `[unverified]`. Rendering to the human remains Anthropic's contract per Step 0.
- `MultiEdit` applies its edits sequentially against an accumulating buffer — the reconstruction helper's running-buffer semantics (line 42) require this specifically, and it is **not** low-blast-radius: it's the reconstruction mechanism itself, exercised on every `MultiEdit` call with ≥2 edits, not one test's expected value. Cite MultiEdit's own tool description (a first-party source, same tier as the citations elsewhere in this ledger) confirming sequential-buffer application before implementing the MultiEdit branch — plan-review flagged this as cheaper to verify than to carry unverified. Separately: whether MultiEdit aborts the whole call on a missing `old_string` partway through is genuinely irrelevant to hook correctness — `PreToolUse` fires prospectively on `tool_input`, before the real tool call resolves, so the hook must model the edits assuming success regardless of what MultiEdit would actually do on failure. That half stays unverified; the buffer-accumulation half does not.

## Critical files

**Create**

- **`claude/.claude/hooks/ask-new-dependency-disclosure.sh`** — `# hook-class: informational`. Filter order is load-bearing (this runs on *every* write for every stow user; nothing else on the `Edit|Write|MultiEdit` matcher spawns a `python3` subprocess today, though several — `ask-review-permissions.sh` among them — already spawn `jq`/`grep`):
  1. `jq` → `tool_name`; not `Edit|Write|MultiEdit` → `exit 0`
  2. `jq` → `file_path`; empty → `exit 0`
  3. **basename `case` against `package.json`** → no match → `exit 0` *(the discriminating filter; must precede every spawn)*
  4. **path-segment exclusion**: `node_modules/` anywhere in the path, or any path under a directory named `fixtures`/`__fixtures__`/`test-data` → `exit 0` — a vendored or test-fixture manifest is not a dependency the human is choosing.
  5. on-disk size guard, over cap → **degraded `ask`** (see below), not silent — a manifest too large to evaluate is a manifest the human should still be told was written to.
  6. **interpreter sanity probe**: `command -v python3` absent, **or present but `python3 -c ''` exits nonzero** (the Xcode Command Line Tools shim, which sits on PATH but fails until CLT is installed) → silent `exit 0`. Both are "the tool to evaluate is unavailable," not "the content is unevaluable," and neither is reachable by crafting a single manifest's content — keep this disposition distinct from step 5's, and make sure the real helper invocation in step 7 is only reached once this probe has passed, so a broken interpreter can't be misclassified as a helper content-parse failure (which would wrongly route to the step-5 degraded-`ask` branch).
  7. only now spawn the helper under `_lib_capped`

  Common path costs two `jq` calls and two `case`s — comparable to what the existing hooks already spend.

  **No repo-scoped opt-out sentinel.** Dropped from this design: a `.claude/*-optout` file the hook itself would be the sole reader of is agent-writable with one `Write` call and produces total, silent, permanent suppression — the same one-step-suppression failure mode the malformed-JSON handling below exists to prevent, and inconsistent with the "prose creates a duty, it isn't enforcement" framing this plan otherwise holds to. This repo's existing repo-scoped opt-outs (`.claude/worktree-optout`, `.claude/autonomous-shipping-optout`) all return the session to a *safer* default; this one would do the opposite. If a specific repo's manifests are generated rather than hand-edited and the hook proves noisy there, that is a follow-up, decided with the engineer in the loop — not a mechanism this PR ships.

  **Emission:** encode via `printf '%s' "$reason" | _lib_jq -Rs .` then `printf` the envelope with `%s` — never `echo` an interpolated payload. `ask-review-permissions.sh:27` is a static literal and gives no escaping model; this hook is the repo's **first to interpolate untrusted content into a decision envelope**. **Disposition is inverted from `_lib_emit_deny`:** where that hard-blocks on encode failure, this one `exit 0`s silently — a half-built payload is worse than none.

  **Fail-open, but loud on a matched, non-excluded manifest.** Steps 1-4 and 6 → silent `exit 0` — none of them is agent-reachable by crafting the manifest's *content*. Step 5 (size), and the helper erroring, timing out, or emitting unparseable output → still non-blocking, but emit `ask` with a degraded reason ("dependency delta could not be determined for `<path>`") — manifest content is agent-authored, so a too-large or malformed manifest is agent-reachable, and silence there is one-step suppression of the reminder. `<path>` is agent-controlled (a directory name is as attacker-authored as a package name) and is the one interpolated value on the degraded branch — apply the same sanitize rule to it as to package names (strip C0/C1 control bytes and ANSI, cap length) before it enters the reason string, and extend the envelope-integrity test to a hostile path in addition to a hostile name.

  **The degraded-`ask` contract needs two more guarantees than "non-blocking":** (1) the shell hook must structurally validate the NUL-separated record stream and its distinct "…and N more" marker before folding either into the reason — an exit-0 wrapper doesn't rule out `_lib_capped`'s output cap truncating mid-record, and a truncated-but-syntactically-plausible record must not reach the human-facing envelope; (2) the CLI wrapper must never let an exception raised mid-computation produce exit 0 with output indistinguishable from a legitimate empty-diff result — any exception path is a nonzero exit, routing to degraded `ask`, never a silent "no new deps" that happens to be wrong. Add a tier-3 test for "helper raises after emitting some but not all records," distinct from "helper fails before emitting anything."

  **`_lib_capped`'s timeout degrades to none without GNU coreutils** — exactly the stock-macOS environment this hook is already scoped for (see the Python-3.9-floor note below). An input-size cap bounds document size, not a hanging interpreter; on a coreutils-less machine a stuck `python3` (the interpreter probe or the real helper) blocks the entire shared `Edit|Write|MultiEdit` matcher for that session, not just `package.json` work. Close this with a runtime bound inside the helper itself, independent of coreutils — Python's `signal.alarm`/`SIGALRM` is the portable stopgap; state the chosen bound in the module docstring alongside the byte-size cap.

  **No `set -euo pipefail`** — 36 of 37 hooks omit it, and the `RESULT=$(...); EXIT=$?` idiom (`require-worktree-for-git-writes.sh:177-178`) is incompatible with `set -e`, which aborts at the assignment before `$?` is read. This drops only `-e`: adopt `set -uo pipefail` per `claude-hook-review`'s canonical new-gate skeleton, not a bare shebang with no safety flags at all. State this in the header, since `claude/.claude/rules/shell-script-conventions.md` prescribes full `set -euo pipefail`. Invoke as `printf '%s' "$INPUT" | python3 "$HELPER"` with a `[ -f ]` precondition on the helper — `parse-git-command.py` is mode 644. Sources `_lib.sh` for `_lib_capped`/`_lib_jq`; a failed source happens before any manifest could have been matched (steps 1-3 need `_lib_jq`), so it is unconditionally silent `exit 0` — the degraded-`ask` branch is unreachable at that point, since the encoder needed to emit it is exactly what failed to load.

  `require-worktree-for-git-writes.sh:166-180` is the model for *invocation shape only*; its disposition is fail-**closed**. Note the inversion explicitly in the header.

  **Size-guard implementation:** the portable byte-count probe is not in `_lib.sh` — it is the file-local `file_size()` pattern in `deny-data-file-reads.sh:113-119` (`stat -c%s || stat -f%z || wc -c`, since bare `stat -c%s` yields empty on macOS). This would be a third copy; cite the pattern by path rather than re-deriving it, and note whether it's worth promoting to `_lib.sh` (author's call, not required for this PR). The guard applies to **on-disk pre-state size only** — an `Edit`'s `new_string` or a `Write`'s `content` is unbounded by it, since a brand-new manifest is 0 bytes on disk. The helper independently caps total stdin size it will parse (state the byte value in the header comment) and treats an over-cap payload the same as a parse failure — degraded `ask`. Give this cap the same boundary-test treatment as the 10-name cap below: a `cap`/`cap+1`-byte test pair, not just the two size-guard branches exercised qualitatively.

- **`claude/.claude/hooks/parse-manifest-dependencies.py`** — two functions, not one, so the reconstruction seam is unit-testable without I/O: `compute_new_dependency_names(pre_text, tool_input) -> list[str]` (pure — takes the pre-state text and the raw `tool_input` dict, applies `Edit`/`MultiEdit`/`Write` semantics in memory, diffs dependency key sets) and a thin CLI wrapper that resolves `file_path` (honoring symlinks and a relative path against the payload's `cwd`) and reads the pre-state file before calling it. The pure function is what `test_parse_git_command.py`'s `importlib.util.spec_from_file_location` pattern calls directly; the CLI wrapper carries only the symlink/relative-path cases.

  Target **Python 3.9** explicitly (not 3.11, not `py312`) — stock macOS `/usr/bin/python3` is 3.9.6. No `match` statements, no PEP 604 `X | Y` unions in runtime position (annotation-position is fine under `from __future__ import annotations`), no stdlib added after 3.9. State this floor in the module docstring; `ruff`'s `pyproject.toml` `target-version = "py312"` (repo-wide, `select` includes `UP`) governs lint style, not the runtime floor, and the two would otherwise silently diverge — CI runs 3.12 throughout (`.github/workflows/tests.yml`), so a 3.10+ construct ships green and only fails on a stow user's stock-macOS interpreter, where the hook's own fail-open would make the failure silent. Close that gap mechanically, not just in prose: a test-tier-1 case parsing the helper's own source with `ast.parse(source, feature_version=(3, 9))` (needs no 3.9 interpreter to run) and asserting no `SyntaxError`.

  Sort/cap/sanitize are pure list transformations and belong in `compute_new_dependency_names` alongside the reconstruction logic, not left unassigned to "the helper module generally" — keeping them in the pure tier is what makes the cap-boundary tests (see test file below) tier-1, not subprocess-tier.

  Emit `name@constraint` pairs, not names alone — the plan's own goal states the human must be told "which version," and the CLAUDE.md bullet demands "exact version constraint." The value is already in hand while diffing key sets; extracting it costs nothing extra. **NUL-separated** (a record containing a newline would corrupt a line-based contract) as a distinct stream from the `…and N more` cap marker — the marker is metadata about the list, never a record that could collide with a real package name or constraint. Sanitize each record: strip C0/C1 control bytes and ANSI from both the name and the constraint, cap length per record. Sort deterministically (by name) before applying the count cap at 10, so which package is elided is reproducible, not an artifact of dict ordering. Module docstring documents the grammar, per `parse-git-command.py`.

- **`claude/.claude/hooks/tests/test_ask_new_dependency_disclosure.py`** — three tiers, mirroring `test_parse_git_command.py`, which imports its helper via `importlib.util.spec_from_file_location` and calls it directly:
  1. **Unit, against `compute_new_dependency_names` (pure, no I/O)** — this is where reconstruction, sanitization, and sort/cap logic are all tested: `replace_all` × `old_string` multiplicity (2×2), including the MultiEdit row's own per-edit `replace_all`; `old_string` absent; empty `old_string` (prepend) and empty `new_string` (deletion); deletion+addition in one edit; pre-invalid/post-valid JSON (an agent repairing a manifest must **not** fire on every dep); post-invalid **pre-state**, asserted against the degraded-`ask` disposition named above, not silent-allow or an empty-diff false-fire; post-invalid; CRLF; BOM; Python-floor syntax check (`ast.parse` with `feature_version=(3, 9)` against the module's own source); cap-boundary at 9, 10, and 11 new names, asserting exact emitted count **and** that the `…and N more` marker is absent at 9 and 10, present only at 11. **Realistic package-shape fixtures** — a scoped name (`@scope/pkg`), a git/URL version constraint (`"dep": "git+https://…"`), and the `workspace:*` protocol string — this hook's target ecosystem (a monorepo, per the originating incident) uses all three, and mechanical JSON-edit permutations alone don't exercise them.

     **MultiEdit, applied against a running buffer (line 42) — this must net out correctly, not merely be pinned as an accepted false positive.** A MultiEdit whose intermediate step names a dependency that a later step in the same call removes again produces an empty key-set diff under the stated running-buffer semantics — the hook correctly does not fire. Assert this as a positive correctness case (`test_multiedit_intermediate_dependency_correctly_nets_to_no_new_deps`, expected value the empty set), not a residual. This also guards the buffer-simulation itself: a helper that (incorrectly) applies each edit against the original pre-state independently, rather than against the running buffer, would see the intermediate addition in isolation and wrongly fire — this test is what catches that regression.

  2. **CLI wrapper, I/O cases only** — symlinked manifest; relative `file_path` resolved against payload `cwd`; stdin/stdout/exit-code contract; NUL separation between `name@constraint` records; the `…and N more` cap marker is a distinct field from the record stream, tested with a package name that collides with the marker's literal text.
  3. **Hook subprocess** — filter behavior (steps 1-7 in order, each exercised independently) and envelope integrity. Drop any test claiming to assert step-3-before-step-6 *ordering* specifically — both terminate in an identical silent `exit 0`, so with `python3` present there is no observable difference between the two orderings and such a test cannot fail on a reordered implementation. Keep the real, falsifiable assertion instead: a non-manifest path must not spawn `python3` at all, checked via `_path_without("python3")` (`test_hook_alignment.py:490`'s precedent) — this *is* observable, since a wrongly-ordered hook that reaches the spawn step errors when `python3` is absent from `PATH`.

  **Fail-open must be asserted, not assumed.** `helpers.run_hook` maps empty stdout to `"allow"` whenever returncode ≠ 2, so a Python traceback reads as a clean pass. Use a file-local `_run_disclosure()` returning `(returncode, stdout, stderr)` and assert `returncode == 0 and stdout == ""` for deliberate-allow (steps 1-4, 6). Include a **positive control** proving the runner detects a nonzero exit, else the runner is unfalsified.

  **Degraded-`ask` must be distinguished from a real one, not merely both asserted as `"ask"`.** For each degraded case (oversized manifest, helper error, malformed JSON), assert the reason's distinguishing substring ("dependency delta could not be determined") *and* that no package name appears in it — paired with a same-fixture *non*-degraded case in the same test proving the ordinary branch is reachable. Otherwise a broken helper that always degrades passes every degraded-case test while the ordinary path silently never fires. Also assert the interpreter-unusable case (step 6's `python3 -c ''` probe failing, e.g. the CLT-shim shape) resolves to *silent* allow, not degraded `ask` — the two are easy to conflate since both originate from "the helper didn't run."

  **Must-not-fire cases** are the high-value ones — a false positive trains click-through and degrades every other prompt: version bump only; `dependencies`→`devDependencies` move; wholesale reformat/re-sort via `Write`; `scripts`, `overrides`, `resolutions`, `engines`, `workspaces` edits; `node_modules/**/package.json` and a `fixtures/package.json` (proving filter step 4); `Package.JSON`/`Package.json` case variants against the case-sensitive `case` pattern in step 3 — deliberate, since the design is stock-macOS-anchored and APFS is case-insensitive-preserving by default, so state and test the intended behavior rather than leaving it to whatever `case` happens to do.

  **Filter-step-4 exclusion needs a precision control, not only a positive match.** `fixtures/package.json` and `node_modules/**/package.json` prove the exclusion fires on the intended targets; nothing proves it does **not** fire on a similarly-but-differently-named directory (`my-fixtures-app/package.json`, `node_modules_backup/package.json`). If the implementation ends up substring-matching rather than exact-path-segment-matching, this is the single largest silent bypass in the hook's own design — for a control whose threat model is "an agent finding the path of least resistance," an unintended-but-adjacent exclusion is the deny-path-equivalent test this control needs. Add the negative case alongside the positive ones.

  **Envelope-integrity tests:** (a) a package name containing `"`, `\`, a newline, a control byte, and the literal `","permissionDecision":"allow"`; (b) the same for a version constraint string; (c) the same for a directory name feeding the degraded-`ask` path's `<path>` interpolation — assert in each case that raw stdout `json.loads`-parses, the value round-trips, and the decision is `ask`.

  **Also required** (`test_hook_alignment.py` Layer 2 does *not* cover this hook — it is `informational`): `jq` absent → silent allow; `python3` absent from `PATH` → silent allow; `edit_input(..., agent_type="code-writer")` emits an identical `ask`, resolving the ledger's `[unverified]` subagent row for the hook's own behavior (rendering to the human stays Anthropic's contract, per Step 0). Assert the reason names **all** added packages (a `head -1` bug passes a single-name check) and does **not** name pre-existing deps or the `scripts` key.

**Modify**

- **`claude/.claude/CLAUDE.md`** — new `§Safety` bullet after the "Installing new software autonomously…" bullet. Four lines, not eight: the earlier draft restated that bullet's own "a general go-ahead does not authorize it" verbatim and enumerated six manifests the hook does not cover. Verbatim:

  ```
  - **Name every new package before it is fetched.** Causing a package not already
    declared to be fetched — by an install command, a manifest edit, or a bare
    restore run after one — requires stating each package, its exact version
    constraint, and why. For a manifest edit, get explicit confirmation before
    making it. For an install command or restore, this is in addition to — not
    instead of — the installing-new-software prohibition: name the package before handing the
    command to the user via the `!` escape. The package already existing
    elsewhere in the same monorepo or lockfile is not authorization. Upgrades of
    already-declared packages are outside this rule.
  ```

  The "elsewhere in the same monorepo" clause is load-bearing — it is the exact rationalization behind the originating incident.

- **`claude/.claude/hooks/deny-network-installs.sh`** — one change: `_INSTALL_ALTERNATIVE` (line 55) gains the naming requirement; it feeds all seven deny messages, participates in no predicate, and routes through `_lib_jq -Rs .`, so no deny path changes. `test_deny_network_installs.py:435` asserts `"shell escape" in reason` and still passes; add a sibling for the naming clause.

  **`npm pkg set` is a confirmed bypass, deliberately not closed here.** `npm pkg set dependencies.<name>=<ver>` writes a manifest entry today with no deny and no ask (`_install_check_npm_family`'s verb loop does single-token compares, `deny-network-installs.sh:120-132`, and `pkg`/`set` match no listed verb). Closing it in this hook is not a small addition: the verb is a token *pair*, and a naive `pkg` verb addition denies read operations too (`npm pkg get`, `npm pkg delete`) under a message claiming an install — a false-deny on every stow user's read for a control this PR is trying to make more informative, not more disruptive. Named as a residual in `docs/security-hardening.md` with the exact shape (`pkg`+`set` ordered pair, gated on a `dependencies.`/`devDependencies.`/`peerDependencies.`/`optionalDependencies.` value prefix, needing its own allow/deny test matrix) so a follow-up can close it deliberately rather than as a rushed addition to this PR.

- **`claude/.claude/tests/helpers.py`** — `edit_input`/`write_input`/`multiedit_input` hardcode `old_string="a"`, `content="x"`, `edits=[]` and cannot carry manifest content, so no content-dependent test is writable as specified. Add optional content kwargs; existing call sites keep the defaults.

- **`claude/.claude/settings.json`** — add the hook to the existing `PreToolUse` `Edit|Write|MultiEdit` group.
- **`docs/hooks.md`** — **required**: `test_hook_alignment.py` Layer 0 fails without a `- **\`<name>\`**` bullet.
- **`README.md`** — hooks table row (~line 159).
- **`docs/security-hardening.md`** — the manifest-edit surface, the Step 0 spike finding, and the residual list below.

**Named residuals** — documented in the hook header and `docs/security-hardening.md`, never silently missed:

*Not detected by key-set diffing:* `overrides`/`resolutions` repointing an existing name at a different tarball; `scripts.preinstall`/`postinstall` (the `scripts`-must-not-fire test needs a comment saying this is deliberate non-coverage, not a correctness property); registry redirection generally.
*Not on the Edit path:* bash heredoc/`tee`/`sed`/`node -e` manifest writes; `npx create-*` scaffolding (a Bash subprocess writes the manifest, so this hook never fires for it); `git pull`/`merge` importing a manifest change followed by the deliberately-allowed bare restore; `npm pkg set dependencies.<name>=<ver>` (see `deny-network-installs.sh` entry above for the exact shape a follow-up would need). **This last one is the highest-priority follow-up of the whole residual list** — it is the only route that reproduces the originating incident end-to-end (manifest write, then a permitted bare restore fetches it) with no mechanical layer in the path at all, ranking above `requirements.txt` below despite listing later.
*Other:* the 14 `permissions.deny` managers (`brew`, `cargo install`, `go install`, `cargo add`, `bundle add`, `poetry add`, `deno add`, …) inherit nothing from Layers 2 or 3 — for those, only Layer 1 applies; a monorepo dep-move fires on the receiving package while the removal is silent (correct-by-design, but pinned as a named false-positive); N sequential `Edit`s produce N prompts; a brand-new `package.json` fires on every entry up to the 10-name cap; `_lib_capped` degrades to no timeout without GNU coreutils, so the helper carries its own input-size bound; no repo-scoped opt-out (see hook entry above); non-`package.json` ecosystems, including `requirements.txt` — deferred not because of the `tomllib` hazard (which doesn't apply to a line-based format) but because of its own grammar hazards (`-r`/`-e` includes, environment markers, PEP 503 name normalization), and because it is this repo's own primary manifest, closing it is a follow-up worth prioritizing, just below `npm pkg set`; lockfiles.

**Reuse:** `_lib_capped`, `_lib_jq`. *(Dropped `_lib_config_dir` — nothing here resolves a config directory.)* `_lib_parse_tool_input_or_deny` exposes only `TOOL_NAME` and `COMMAND`, so this hook does its own `jq` extraction, as all six existing `Edit|Write|MultiEdit` hooks do.

## Verification

**Step 0 — the spike, before Layer 2 is built.** The question is specifically whether a hook-returned `ask` renders in `acceptEdits` and `bypassPermissions` — not whether it renders at all, since it trivially does in `default` mode and that proves nothing about the gap Layer 2 targets.

1. Set up an **isolated vehicle**: a scratch git repo with no `.claude/worktree-required` and no other hook on the `Edit|Write|MultiEdit` matcher that could deny first — copy only `ask-review-permissions.sh` and `_lib.sh` into `.claude/hooks/`, wire the matcher in a scratch `.claude/settings.json`, so a deny from an unrelated hook (e.g. `require-worktree-for-file-writes.sh`, which *would* fire in this repo's own worktree-enforced main tree) can't be misread as "ask doesn't render."
2. In that scratch repo, edit `.claude/settings.json` under `acceptEdits` mode. Observe: does an interactive prompt appear, or does the edit silently proceed?
3. Repeat under `bypassPermissions`.
4. **Decision rule:** Layer 2 ships if `ask` renders in `acceptEdits`, regardless of the `bypassPermissions` result — `bypassPermissions` is documented to skip permission checks wholesale, so a negative result there is expected and not disqualifying. Layer 2 is dropped only if `acceptEdits` also fails to render it.
5. Record the finding in `docs/security-hardening.md`, dated and tagged with the Claude Code version tested — this is an empirical observation of a vendor-undocumented contract and can change silently on a future release.

**If Step 0 finds `ask` does not render in `acceptEdits`** — the deliverable is Layers 1+3 only: the `claude/.claude/CLAUDE.md` bullet, the `_INSTALL_ALTERNATIVE` amendment and its sibling test assertion, and the `docs/security-hardening.md` spike record — the `npm pkg set` residual note stays documented there regardless of which branch ships, since it's a gap in `deny-network-installs.sh` independent of Layer 2's fate. No hook, no Python helper, no test file, no `settings.json` matcher change, no `docs/hooks.md`/README rows. Conversely, if Layer 2 ships now and is reverted later, `test_hook_alignment.py` Layer 0 couples the hook file, its `docs/hooks.md` bullet, and its live `settings.json` matcher entry — a revert that drops one but not the others fails that gate, so treat all three as one unit in either direction.

Implementation runs in a linked worktree, and the `.venv` exists only at the main worktree root:

```bash
../../../.venv/bin/pytest claude/.claude/
../../../.venv/bin/ruff check claude/.claude/
scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck
```

**`git add` both new files first** — `list-shell-files.sh` enumerates via `git ls-files`, so an untracked hook is invisible to shellcheck and reports green. `test_hook_alignment.py` is the gate that matters: it independently enforces the `docs/hooks.md` bullet, the `hook-class` line, and the live `settings.json` matcher entry.

End-to-end in a scratch directory, for the one thing pytest cannot cover — whether the reason string reaches a human:
1. Seed a `package.json` with one dependency; add a second with a version constraint → the prompt names it as `name@constraint`.
2. Bump the existing dependency's version → no prompt.
3. Edit `scripts` only → no prompt.
4. Scaffold a fresh `package.json` with 12 deps → prompt caps at 10 with `…and N more`.
5. A genuine bare `npm install` against an unchanged manifest → nothing fires.

## Out of scope

- **The moratorium sentinel** from the originating brief — dropped by decision; a per-instance ask already gives the engineer the control point a moratorium would apply at.
- **Version upgrades, lockfile and transitive changes** — by decision. Both carry real supply-chain exposure; lockfile disclosure would be high-volume and largely unreviewable.
- **Non-`package.json` ecosystems** — follow-up, once the false-positive rate is observed.
- **Making named installs promptable** — they stay hard-denied; this change only makes the handoff informative.
- **Closing the registry-redirect and install-script shapes** (`overrides`, `--extra-index-url`, `postinstall`) — a different trigger model than key-set diffing; documented as residuals here.
- **Strengthening `code-review` item 9e** — post-hoc review cannot prevent a fetch-time compromise, so it does not substitute for this.
