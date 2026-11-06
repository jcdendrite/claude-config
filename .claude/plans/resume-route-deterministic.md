# Make the resume-context route a deterministic lookup and the announced commands copyable

## Context

Goal: make the choice between slash `/resume-context`, shell `resume-context --cwd`, and `/compact` unambiguous for models, and make the announced resume commands easier to copy on a phone.

Ask: "the /resume-context text that prints out in the hook doesn't come in a code block so it's hard to copy on my phone" and "the models still aren't being clear about when to use compact versus resume-context within session versus resume-context outside of session. Choosing compaction does require model judgment, but resume-context should be deterministic - it completely depends whether or not the worktree is changing or not." Later, on where it went wrong: "I think it was just after the nudge fired, and I think the handoff write just emitted text that said \"if you want to start in a new session\" without making a recommendation based on whether a different worktree was needed." On rendering: "on the phone the backticks don't appear untranslated and they make the render super easy to copy. This is what already happens with the full resume-context command when the agent remembers to print that out." After the render probe, on what the phone showed: "I would guess you can’t use fences like the three backs I think the best that you can do is use the single back which at least will make it somewhat easier for me to copy and maybe you can also put it on a new line? And then make sure that when the model does output it in chat that it’s fenced just like the full resume-context command"

Render probe result (the engineer's phone, one scratch `PostToolUse` hook on `Write`, a `systemMessage` in the earlier three-backtick shape): the client prefixed every line of the message with `PostToolUse:Write says:` and drew the message as a warning block. The fence lines came out as stray prefixed fragments around a code block whose command line read `PostToolUse:Write says: /resume-context probe-handoff.md`. The terminal result is not yet known. The model's own text in the same screenshot rendered an inline code span cleanly.

Why now: the engineer reports models still pick the wrong route. Verified so far: `_lib_resume_context_verdict` in `claude/.claude/hooks/_lib.sh` already decides slash vs shell from one variable (the file's `--cwd` equals the session's linked worktree root). Worktree enforcement does not itself require a new session to change worktrees. The model-facing prose that states the rule is restated in several places and is not phrased as a lookup.

Intended outcome: one canonical statement of the route rule that every other site defers to, a hook announce message that sets its one command alone on its own line in single backticks, which the engineer expects to be only "somewhat easier" to copy, a model instruction to relay that command in a triple-backtick block, and tests that pin the message and the instruction's wording. Whether models follow the instruction is measured only by the follow-up in Out of scope.

## Approach

Every resume announcement names one route, picked by the comparison `_lib_resume_context_verdict` already makes: does the directory in the written file's own resume command equal this session's worktree. That route's command sits alone on the second line of the hook's `systemMessage`, in single backticks, and the model is told to relay that one route with the command in a fenced code block (rows 36, 37). Two further edits make the comparison answer the engineer's question, whether the worktree is changing: handoff §7 and brief §7.5 set `--cwd` to the directory the resumed session starts in, and the nudge states the same rule before the handoff is written. `/compact` against `/handoff` stays the existing three-clause judgment.

The resulting order, for the model at nudge time and for the engineer:

1. The next step runs in another worktree or repository: `/handoff`, because the first `/compact` clause already fails. Resume outside this session with the shell command the announce gives, in a terminal on the machine that holds the file.
2. The next step runs in this worktree: `/compact` when all three clauses hold, judged as today. Otherwise `/handoff`, then resume in this session: `/clear`, then `/resume-context <file name>`.

A brief keeps the same lookup. Its work is for another session, so its same-worktree announce offers a new session in this worktree before `/clear` (row 28).

**Trade-off of one route per announce.** A same-worktree announce no longer shows the shell route (row 29). That route keeps the file in the system prompt, where a later auto-compaction cannot summarize it. It also binds project hooks to the worktree, which a session that entered its worktree with `EnterWorktree` does not get after `/clear` (G3). Asked whether to accept the trade-off, the engineer replied "I’m inclined to accept, yes." The reply is hedged, so the plan reads it as accepting this trade-off and nothing wider. The shell route stays reachable: `docs/skills.md` tells the engineer how to choose it by hand, and the slash script prints it whenever it cannot load (row 10). It needs a terminal on the machine that holds the file, so a phone without one can resume only in-session.

Alternatives set aside:
- **Keep the three-backtick fence in the hook messages:** on the phone the client prefixed every line, fence lines included, so the fences showed as stray fragments and the block's one line carried the prefix (row 38). The engineer chose single backticks, with the command on a line of its own, instead (row 36).
- **Single backticks with the command on the LEAD's line, or a blank line between them:** the engineer asked for the command on a new line (row 36), and alone on its line it stays the easiest to select if the backticks do not render (row 5). A blank line would carry the client's prefix too and show as one more stray fragment (row 38).
- **Single backticks in the model's relay too, for one shape on both surfaces:** the engineer asked that the relay stay fenced like the full resume-context command (row 37), the form row 4 reports renders well.
- **Change the hook message's shape on its own when a surface renders it badly:** the engineer chose to be asked (row 32), and has answered once already (rows 36, 37).
- **Key handoff §7 on §3's first command:** §3 may be a file edit, a dispatch or a question, which runs no command, so §7 keys on the first step and gives it a default (row 34).
- **Keep the shell command as a second option in a same-worktree announce:** that two-route menu is what the post-write relays repeated (rows 8 and 9). The slash route's own script prints the shell command whenever it cannot load (row 10), so no route becomes unreachable. The cost is the trade-off above.
- **Announce briefs on their own rule, always shell:** row 1 makes the route depend only on whether the worktree changes, so only the brief's `load` wording changes (row 28).
- **Leave the nudge silent on resuming, as its pinned design does today:** the engineer placed the failure at nudge time too (row 8), and the nudge is where the three-way choice is made.
- **Restructure `COMPACT_CRITERIA` around the worktree question:** that rewrites vendor-grounded constants and their doc quotes. One added sentence states the same order.
- **Make `resume-context/SKILL.md` or `docs/handoff-nudge.md` the rule's canonical home:** the skill body loads only when the engineer types `/resume-context`, and 16 of its clauses are pinned (`claude-skills/skills/tests/test_skills.py:5838-5927`). `docs/handoff-nudge.md` owns the `/compact` criteria, not resume routing. `docs/skills.md`'s `/resume-context` entry already calls itself the canonical home, and four sites already point to it (row 13).
- **Shell-quote the consume hook's reload destination:** the engineer chose to drop the quoting (row 31).

**Root:** After a nudge and after a handoff write, models present the resume commands as a menu, or give only the shell command, instead of one route chosen by whether the worktree changes. The hook also prints the slash command mid-sentence with no code span, where a phone cannot copy it cleanly. Its shell command is already a mid-sentence single-backtick span, which this change moves to its own line (row 3).

**Givens** (fixed, beyond this design's reach):
- **G1.** How a client renders a hook's `systemMessage` belongs to Claude Code, the prefix it puts on every line included. This repo chooses only the text. (rows 5, 38)
- **G2.** Only the user can run `/clear` and `/compact`, because the harness gives the agent no tool binding for either. The vendor owns that binding; `docs/handoff-nudge.md:38` records it. (rows 1, 2)
- **G3.** A worktree entered with `EnterWorktree` and then `/clear`ed keeps project hooks bound to the launch directory (`docs/skills.md:29`, one probe). That is why a resume into another worktree takes the shell route. The harness owns hook binding. (row 1)
- **G4.** `~/.claude/hooks` resolves through stow to the main checkout, so a session in the feature worktree runs today's hooks until merge. Every consumer of `./install.sh` shares that layout, and changing it needs a decision outside this plan. (row 23)

**Mechanisms** (each anchored):
- **M1:** `announce-resume-command.sh` names one route per verdict. On `load` it announces only `/resume-context <file name>`, worded for the file's kind: a handoff resumes in this session after `/clear`, and a brief in a new session in this worktree or after `/clear`. Otherwise it announces only the shell command, after today's reason sentence. Every `systemMessage` is two lines, `<LEAD><NL><TICK><CMD><TICK>`: a one-line LEAD, then the command alone in single backticks (row 36). One site after the branch assembles it. No delimiter line is added, because the backtick pair delimits the command inside its own line, and every line of the message carries the client's prefix (row 38). Wording is under "Announce wording per branch" below. A re-probe or step 6 result that renders this shape badly goes to the engineer and changes nothing on its own (row 32). `anchors: row1`
- **M2:** `additionalContext` stays one line. It names the route, the same reason the LEAD gives, and its provenance. It tells the model to relay that one route with the command alone in its own fenced code block, name no other resume route, and run nothing. The engineer asked for that fence in chat (row 37), and markdown in the model's own text renders as code on the phone (rows 4, 38). `anchors: row4`
- **M3:** handoff §7 and brief §7.5 set `--cwd` to the directory the resumed session starts in. That is the root where the first step runs, the existing directory a worktree-creating step runs from (row 27), or a default when the first step names no directory (row 34). The bare form remains only in the case row 26 allows. Handoff §4 sends the resuming session to §7's directory. Exact text is under "Skill text" below. `anchors: row7`
- **M4:** the nudge hook gains a `HANDOFF_RESUME_ROUTE` constant, emitted in both messages, whose last sentence makes the post-write message authoritative. The advisory message also tells the model to name the route when it recommends `/handoff`, and to run no resume command itself. M3 makes the file's `--cwd` the same fact this sentence asks about, so the nudge-time route and the announce agree whenever §7 follows its rule. `anchors: row8`
- **M5:** the rule keeps one human-facing home, `docs/skills.md`'s `/resume-context` entry, restated as a lookup per "Docs" below. `docs/handoff-nudge.md` quotes the new constant, as it quotes the other four, and links to that entry. `docs/hooks.md`'s announce entry follows the header edits. `anchors: row13`
- **M6:** sibling arms of the same bug shape, a command printed for the engineer to copy without being set off as code, get the fix their surface takes. `anchors: row14`
  - `consume-durable-continuity-file-on-read.sh`'s reload command moves, as it is today, into M1's two-line shape (rows 19, 31, 36).
  - `resume-context/SKILL.md` gains one sentence telling the model to fence each command it relays (rows 20, 37).
- **M7:** tests change where they pin the old wording or the one-line `systemMessage`, and each keeps the property it guards (row 15). The new tests, listed under Critical files, defend wording and structure, not whether models now route correctly. `anchors: row15`
- **M8:** two `code-writer` dispatches, in sequence, in one PR, with the file sets under Critical files. The file sets do not overlap, and the plan's prescribed text specifies each without the other's context. `anchors: root`

**Over-powered-primitive check.** No mechanism adds machinery: M1 rewords strings around the existing verdict, and M4 is a static string. M4 is the one place a heavier choice was on offer, a nudge that detects a worktree change. Primitives weighed:
1. **A static conditional sentence (chosen):** it hands the model the rule, and the model already judges the next step's worktree under the first `/compact` clause. `anchors: row8`
2. **Leave the nudge unchanged:** cheapest, but row 8 places the failure at nudge time. `anchors: row8`
3. **Detection inside the nudge:** at nudge time no continuity file exists, so `_lib_resume_context_verdict` has no file `--cwd` to compare. Detection could only guess where the next step runs. `anchors: row6`

**Announce wording per branch.** Notation: `<NL>` is a newline and `<TICK>` is one backtick. `{root}` is `$WORKTREE_ROOT`, `{name}` is `$FILE_NAME`, `{scope}` is `$SCOPE_NAME`, and `{cmd}` is `$RESUME_COMMAND` as each arm sets it today. Backticks inside a value are literal. A file's kind is brief when `$FILE_PATH` matches the existing glob's `briefs/*-task.md` arm, otherwise handoff.

- **`load` reason `{R}`.**
  - Linked: ``the file's resume command targets this worktree (`{root}`)``.
  - Not linked: `the file's resume command names no directory, and this session is not in a linked worktree`.
  - Neither holds `--cwd` or two adjacent backticks, which the nine-arm test checks (Critical files).
- **`load`, handoff.**
  - LEAD: `Resume in this session, because {R}. Run /clear, then:`
  - CMD: `/resume-context {name}`
  - ROUTE: ``in this session: the engineer runs /clear, then `/resume-context {name}`, because {R}.``
- **`load`, brief** (row 28).
  - LEAD: `Resume in a session in {scope}, because {R}. Start a new claude session there, or run /clear in this one once its work is done, then:`
  - CMD: `/resume-context {name}`
  - ROUTE: ``in a session in {scope}: the engineer starts a new claude session there, or runs /clear in this one once its work is done, then runs `/resume-context {name}`, because {R}.``
- **Shell reason `{REASON}`.**
  - `unknown`: today's sentence, `/resume-context cannot confirm this file belongs to this worktree, because this session's worktree could not be determined.`
  - Mismatch, file target usable or not: today's `/resume-context cannot confirm this file belongs to {scope}, because {MISMATCH_REASON}.`, with all four `MISMATCH_REASON` strings unchanged.
- **Shell, either kind.**
  - LEAD: `{REASON} Resume outside this session instead, in a new terminal:`. It opens with what the hook could not confirm, so an `unknown` result, or a main-checkout file naming its own directory, does not read as a worktree change.
  - CMD: `{cmd}`
  - ROUTE: ``outside this session: the engineer runs `{cmd}` in a new terminal. {REASON}``
- **`additionalContext`, every branch:** `The resume route for the continuity file just written is {ROUTE} {PROVENANCE} -- use the command verbatim instead of reconstructing it. Relay this one route and its reason to the engineer with the command alone in its own fenced code block, name no other resume route, and run nothing yourself.`
- **`{PROVENANCE}`.**
  - On `load`: `The command names the written file by its file name, and the route compares the working directory of this session with the file's own resume command`. The slash command carries no measured path, so the session-measured sentence would claim a measurement that did not happen.
  - On the shell arms: today's two `CWD_PROVENANCE` strings, with "They were computed" changed to "The command was computed". The rest stays, so `_SESSION_MEASURED_PROVENANCE` and `_FILE_SOURCED_CWD_PROVENANCE` still match there.
- **Implementation hazards**, for the dispatch prompt:
  - Each backtick around CMD is written `` \` `` inside the double-quoted assembly, the escape the file already uses on ten lines at :163-186, so no new literal or suppression is needed.
  - An unescaped backtick pair there is a command substitution. On a shell arm it executes the announced command. The hook tests pass `PATH` through (`test_announce_resume_command.py:156`), so that runs the real `resume-context`, which moves the file and `exec`s `claude` (`resume-context.sh:380`). Under `set -uo pipefail` with no `-e`, the hook then emits a mangled message silently.
  - The code-writer therefore runs `shellcheck` on the hook before its first `pytest` run, or sets `RESUME_CONTEXT_LAUNCHER` to a no-op for that run (`resume-context.sh:38-39`).
  - The message is assembled as ``"$LEAD"$'\n'"\`$CMD\`"``.
  - Every arm assigns LEAD, CMD, ROUTE and PROVENANCE. `set -u` aborts the hook on an unset one, which breaks the header's fail-silent posture.
  - The `# shellcheck disable=SC2016` comment at :189 names `$commands`, which becomes `$route`.
- **Header and `docs/hooks.md`.** Each edit site, old wording to new:
  - Header :18, "It also advertises /resume-context <file name> when", becomes "It announces only /resume-context <file name> when". The rest of :18-32 stays.
  - After :32, two lines join the header: "A brief's `load` announce offers a new session before `/clear`, because a brief is work for another session." and "`systemMessage` puts the announced command alone on its second line in single backticks, not in a fenced block, because the phone client prefixes every `systemMessage` line, fence lines included."
  - Header :35, "additionalContext also tells the model to relay the commands and run none of them.", becomes "additionalContext stays one line and tells the model to relay that one route, its command fenced, and run nothing."
  - Header :14-15 and the matching sentence in `docs/hooks.md:192`, "A main-tree .cwd whose file names no usable target, or an unresolvable .cwd, emits the bare command instead, matching what those skill sections prescribe.", become "On the shell route, a main-tree `.cwd` whose file names no usable target, or an unresolvable `.cwd`, gets the bare command." This sentence now holds only on the shell route.
  - In `docs/hooks.md:192`, "announces its `resume-context` command on both hook output channels" becomes "announces one resume route on both hook output channels".
  - `docs/hooks.md:192` also receives the header's two new lines and its new :35 line, placed directly after that sentence. The header gets them at the sites above.
  - A new known-gap item follows the drifted-session gap at header :74-76 and `docs/hooks.md:193`: "A session whose `.cwd` drifted out of its worktree gets the shell route for a file naming that worktree, because the hook compares the payload `.cwd`, not where the work lives."

**Nudge sentence.** `HANDOFF_RESUME_ROUTE` sits beside the four `COMPACT_*` constants (:195-198), under their comment, widened to name it. Both messages carry it:
- **Advisory (:732):** a new `--arg route` carries it. The filter's `$tieBreak + " Only the user can run /compact, ...` becomes `$tieBreak + " If you recommend /handoff, tell the user which resume route applies, and run no resume command yourself. " + $route + " Only the user can run /compact, ...`.
- **Hard block (:717-718):**
  - The fragment `run /handoff now. Only the user can run /compact.` becomes `run /handoff now. %s Only the user can run /compact.`.
  - The `%s` has a space on each side.
  - `"$HANDOFF_RESUME_ROUTE"` joins the `printf` argument list directly after `"$IGNORED_COUNT"`.
  - The detector substring pinned at :2268-2272 stays intact.

The constant:

> After /handoff, the resume route depends only on whether the next step runs in this worktree: if it does, the user resumes in this session with /clear and then /resume-context; if not, the user resumes outside this session with the shell resume-context command in a new terminal. The message shown after the handoff file is written gives the exact command and is authoritative where it differs from a route named earlier.

Its properties:
- It holds no `%`, no apostrophe, and none of "cheaper", "less expensive" or "costs less", so the printf constraint and the cost-comparison test (:2289-2295) hold.
- It speaks of "the user" because the hard block reaches the user with no model composing a reply (row 17).
- The advisory's "run no resume command yourself" is narrower than the announce's "run nothing yourself". The model runs `/handoff` itself when the user picks it (`docs/skills.md:23`, "Model-invocable by exact name"), and an unscoped clause would bar that.

**Skill text.** Handoff §7 (:133-139, seven lines) becomes the two lines below, with a blank line between them so the first line's pin can end there:

> Name the directory the resumed session starts in: the root of the worktree or checkout where §3's first step runs, or that directory itself outside any repository; for a step that creates a worktree, the existing directory it runs from; and for a first step that names no directory or is only a question, this session's own directory, as §4's header names it; then write `resume-context --cwd <worktree path> <config-dir>/handoffs/<slug>-handoff.md`, or, only when that directory is the one this session runs in outside any linked worktree and §4's header names it too, `resume-context <config-dir>/handoffs/<slug>-handoff.md` alone.
>
> The announce message after the write picks the resume route by comparing this directory with the session's worktree. `--cwd` makes the launched session start in that directory regardless of where the resume command is actually run from — do not use a `cd <path> &&` prefix instead, which only works if the invoker happens to already be positioned to run it. Moves the file to a fresh temp path and launches a new session with it loaded, so nothing depends on the resuming session remembering to read or delete the file. Can be aliased for convenience.

Brief §7.5 (:97-103) takes the same shape, with its own nouns:

> Name the directory the session picking up this brief starts in: the root of the worktree or checkout where §6's first step runs, or that directory itself outside any repository; for a step that creates a worktree, the existing directory it runs from; and for a first step that names no directory or is only a decision, the worktree §3 names, else this session's own directory; then write `resume-context --cwd <worktree path> <config-dir>/briefs/<slug>-task.md`, or, only when that directory is the one this session runs in outside any linked worktree and §3 names no other directory, `resume-context <config-dir>/briefs/<slug>-task.md` alone.
>
> The announce message after the write picks the resume route by comparing this directory with the session's worktree. `--cwd` makes the launched session start in that directory regardless of where the resume command is actually run from — do not use a `cd <path> &&` prefix instead, which only works if the invoker happens to already be positioned to run it. Moves the file to a fresh temp path and launches a new session with it loaded, so picking up the brief doesn't depend on the fresh session remembering to read or delete the file. Can be aliased for convenience.

Each clause of the first lines rests on a row:
- the first step's directory, which picks one directory when the next step spans two: row 1;
- the existing directory for a worktree-creating step: rows 27 and 33;
- a default for a first step that names no directory: row 34;
- "outside any linked worktree": row 6;
- the bare-form conditions: row 26.

A handoff's default is this session's own directory as §4's header names it, so a drifted shell does not decide it. A brief's default is §3's worktree first, because its work may live elsewhere.

In handoff §4 (:121), "name that path and direct the resuming session to re-enter it before" becomes "name that path as where these changes live, and direct the resuming session to re-enter §7's directory, where it resumes, before". `resume-context/SKILL.md` gains one paragraph after :9: "Give the engineer each command this skill has you relay or suggest alone in its own fenced code block."

**Docs.** `docs/skills.md:25-27` become the lookup below. Lines :28 and :29 (where each path loads the file, and the `EnterWorktree` hooks caveat) follow the slash bullet unchanged, and :30 (the trigger and listing notes) stays last.

- Lead, replacing :25's text: load a pending `/handoff` or `/brief` file into the current session, after `/clear` or in a new session started in the file's worktree. This entry is the canonical home for which resume route applies. The route is a lookup on one fact, the directory the file's own resume command names, compared with the worktree of the session that would resume it. `handoff/SKILL.md` §7 and `brief/SKILL.md` §7.5 set that directory to where the resumed session starts, so the lookup answers whether the worktree changes. A step that creates a worktree is routed by the existing directory it is created from.
- **Slash bullet:** the **slash** `/resume-context [<slug> | <file name> | <absolute path>]` applies in two cases: a session in a linked worktree reads a file naming that worktree's root as its `--cwd`, or a session outside any linked worktree reads a file naming no `--cwd`. Run it in this session after `/clear`, or in a new `claude` session started there. Then today's description of `resume-context-same-session.sh` and its no-argument behavior, unchanged.
  - :28 and :29, unchanged.
  - A new sub-bullet: "To keep the file in the system prompt, or to get the worktree's own hooks after an `EnterWorktree`, run the shell route by hand instead: `resume-context --cwd <this worktree> <path>`."
- **Shell bullet:** the **shell** `resume-context [--cwd <dir>] <path>` applies in every other case: a file naming another directory, a linked session whose file names no usable `--cwd`, a session outside any linked worktree whose file names any directory (its own included), and a session whose worktree cannot be determined. Run it in a new terminal on the machine that holds the file. It moves the file to a temp path and starts a new `claude` process with it loaded into the system prompt.
- **Announce bullet:** the announce message after a `/handoff` or `/brief` write applies this lookup (`_lib_resume_context_verdict` in `claude/.claude/hooks/_lib.sh`) and names only the route that applies, with its command alone on its own line in single backticks. It compares the file's `--cwd` as written, so a symlinked or dotted spelling of this worktree gets the shell route, though the slash script resolves the spelling and would load it. It compares against the session's working directory as the hook payload reports it, so a session that drifted out of its worktree gets the shell route for a file naming that worktree.
- Whether to write a handoff at all, or `/compact` instead, is a separate judgment, linked to [`docs/handoff-nudge.md`](handoff-nudge.md) "Compaction as an alternative to /handoff" in the style `docs/hooks.md:185` already uses.

**Assumption rows:**

1. `[engineer-verified: "resume-context should be deterministic - it completely depends whether or not the worktree is changing or not."]` The resume route is a function of one fact, whether the next step runs in this session's worktree. Same worktree means the in-session `/resume-context`; a changing worktree means the shell `resume-context`.
2. `[engineer-verified: "Choosing compaction does require model judgment"]` `/compact` against `/handoff` stays a judgment. The four `COMPACT_*` constants do not change.
3. `[engineer-verified: "the /resume-context text that prints out in the hook doesn't come in a code block so it's hard to copy on my phone"]` The hook's printed `/resume-context` command is hard to copy on the phone, because it is not set off as code. Row 36 settles the form the hook message takes.
   - `[verified: claude/.claude/hooks/announce-resume-command.sh:163, :166, :186]` The slash command is the one printed with no code span. The shell command is already a mid-sentence single-backtick span, which this change moves to its own line.
4. `[engineer-verified: "on the phone the backticks don't appear untranslated and they make the render super easy to copy. This is what already happens with the full resume-context command when the agent remembers to print that out."]` Markdown code the model writes renders as copyable code on the engineer's phone.
5. `[unverified]` Whether a hook `systemMessage` in M1's two-line shape shows the command as code on a line of its own, on the phone or in the terminal.
   - Row 38 observed only the three-backtick shape, and only on the phone. No surface has shown the two-line shape, and the terminal has shown neither.
   - Existing announces on the engineer's phone and terminal are free evidence for part of this: each already prints the shell command in a mid-sentence single-backtick span (row 3).
   - M1's copy benefit inherits this flag. M2's fenced relay rests on rows 4 and 37 instead. A command alone on its own line stays easier to select than one mid-sentence, even if its backticks show literally.
   - Verification step 0's re-probe resolves it before dispatch 1 while the probe still runs, and step 6 observes it after merge otherwise (row 32).
6. `[verified: claude/.claude/hooks/_lib.sh:5400-5431; call sites announce-resume-command.sh:150, resume-context-same-session.sh:299; inputs announce-resume-command.sh:120-150, resume-context-same-session.sh:156-171, :184-192; claude/.claude/hooks/tests/test_announce_resume_command.py:361-398]` `_lib_resume_context_verdict` prints `load` only when a linked session's root equals the file's `--cwd`, or when a session outside a linked worktree reads a file naming no `--cwd`. Every other case, `unknown` included, prints `shell`.
   - The announce and the slash script share this one function but feed it different inputs. The hook passes the payload `.cwd` (`announce-resume-command.sh:120-131`) and the file's raw `--cwd` text. The script passes the Bash `$PWD` and the canonicalized target. With the same session directory and a canonically spelled `--cwd`, the two agree, so no new decision code is needed.
   - A symlinked or dotted spelling of this worktree makes the announce say shell where the script loads. Two tests pin that divergence (:361, :382).
   - A payload `.cwd` that differs from the script's `$PWD` can split them either way, and when the script refuses, it prints `SHELL_COMMAND:` (row 10). A session whose `.cwd` drifted out of the worktree its file names gets the shell route, which the header's drift gap (:74-76) does not cover.
7. `[verified: claude-skills/skills/handoff/SKILL.md:121, :133; claude-skills/skills/brief/SKILL.md:97; claude/.claude/hooks/announce-resume-command.sh:178-185]` Today handoff §7 takes its `--cwd` from §4, the worktree this session's files were in, not where the next step runs. A next step in another worktree therefore gets the `load` verdict, against row 1. Brief §7.5 keys on §3's worktree, the brief's own work, but writes the bare form when §3 names none. From a linked session, that bare form gets the session's own worktree as `--cwd` through the announce's fallback, not the checkout the work starts in.
8. `[engineer-verified: "Nudge fired"]` and `[engineer-verified: "After handoff write"]`: the labels selected when asked at which moment the model's explanation went wrong.
   - `[unverified]` The dispatching session's small transcript sample, summarized qualitatively and not re-run here: after handoff writes, relays never fenced a command and never chose a route by worktree. After nudges, replies rarely said anything about resuming. The exact phrase in the engineer's report was not found in that sample.
9. `[verified: claude/.claude/hooks/announce-resume-command.sh:163-164]` The `load` announce today offers the slash command "after /clear, or in a new session started here" and a shell command "in a new process" side by side, with no recommendation.
   - `[unverified]` That this pairing produced the relayed "if you want to start in a new session" is an inference.
10. `[verified: claude-skills/skills/resume-context/SKILL.md:41-53]` When the slash route cannot load, the script prints `SHELL_COMMAND:` and the skill relays it for a new terminal. Candidates outside this worktree get their shell command too. Dropping the shell command from a same-worktree announce therefore leaves no route unreachable.
11. `[verified: claude/.claude/hooks/tests/test_resume_command_template_contract.py:22-60, :86-97]` The §7/§7.5 template must keep four things, and the M3 text keeps all four, so this test runs unchanged:
    - the `<worktree path>` placeholder;
    - a first sentence ending at the first "alone.", with exactly two backticked commands, the `--cwd` form first;
    - no other `<...>` token in that sentence;
    - a following paragraph that mentions `--cwd`.
    - M3's blank line between the two lines changes none of the four, because the test reads the section up to the next heading. The second line holds no `resume-context` word, so the `unknown` answer for a bare command followed by it holds (:86-97).
    - `[verified: claude/.claude/hooks/_lib.sh:5251-5253; test_resume_command_template_contract.py:16, :70]` The test runs `_lib_continuity_file_resume_cwd`, whose scan joins a line to the next only when it ends in a backslash. A hard wrap inside a first line therefore splits the command and fails the test.
12. `[verified: claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py:88-107, :2224-2236, :2281-2287, :2331-2348]` The nudge's pinned design keeps `/clear` and `/resume-context` out of both messages. Each nudge constant is pinned as a substring of the live output and of `docs/handoff-nudge.md`, and as holding no `%`. M4 replaces the first test and extends the other two.
13. `[verified: docs/skills.md:23, :25; docs/handoff-nudge.md:76; docs/scripts.md:377; README.md:507]` `docs/skills.md`'s `/resume-context` entry calls itself the canonical home for the two resume paths. Four other sites point to it rather than restate it. The entry is a bullet, not a heading, so sites name it as those four do, not with the section-sign citation form `.claude/rules/citation-grammar.md` defines for headings.
14. `[verified: claude/.claude/hooks/consume-durable-continuity-file-on-read.sh:141; claude-skills/skills/resume-context/SKILL.md:33, :49, :53]` Two more surfaces hand the engineer a command to copy without a code block: the consume hook's "Reload with:" `systemMessage`, and the skill's relays (recovery text, `SHELL_COMMAND:`, candidate commands).
    - `[verified: claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py:133, :418]` Its tests check `dest in systemMessage`, which the two-line message still satisfies. No test under `claude/.claude/` asserts a one-line `systemMessage` except the announce test in row 15.
    - `[verified: claude-skills/skills/tests/test_skills.py:5838-5927]` The new skill paragraph sits after :9, away from every pinned clause.
15. `[verified: claude/.claude/hooks/tests/test_announce_resume_command.py:80-95, :259-334, :340-413, :423-534, :536-628, :677-766; claude/.claude/scripts/tests/test_resume_context_same_session.py:1188-1246]` The tests that pin today's wording or the one-line message, and what each keeps guarding:
    - :714 asserts no newline in `systemMessage`, to prove a stripped trailing newline cannot reach the output. It becomes `code_span_command(message) == f"resume-context {stripped_path}"` beside the NUL checks. :715, for `additionalContext`, stays.
    - :271-283 and :320-334 are `load` cases, a fixture with no resume command in the main tree or in a non-repo directory:
      - Both assert the announced command is `/resume-context example-handoff.md`, with `--cwd` absent from both channels.
      - :320 gains the `GIT_CEILING_DIRECTORIES` that :605 sets, so a temp root inside a linked worktree cannot turn it into a shell case.
    - :259, :285, :296 and :750 are shell cases and pass as written.
    - :340-359 (a handoff) and :400-413 (a brief) keep only what the nine-arm test does not assert: the announced command is the slash command for their fixture, and the shell command they build today is absent from both channels. Their LEAD, reason and provenance assertions, :356 included, move to the nine-arm test, which owns that wording.
    - :622-628 and the helper at :95 assert the new relay sentence and "use the command verbatim instead of reconstructing it".
    - The round-trip helpers at `test_resume_context_same_session.py:1189` and :1215 become one helper that reads the announced command. The parity assertion at :1245 stays.
    - Every other assertion stays. The wording above keeps the reason sentences, the backticked paths, and the absence of "different", "names a directory", "from `" and, on the main-tree arms, `--cwd`.
16. `[verified: claude-skills/skills/handoff/SKILL.md read at 200 lines; claude-skills/skills/brief/SKILL.md read to its end at 190 lines; claude-skills/skills/resume-context/SKILL.md read at 59 lines; claude/.claude/hooks/check-skill-length.sh:11, :140]` handoff/SKILL.md sits at the 200-line default cap. M3 turns §7's seven lines into three, two lines and the blank one between, leaving 196. Brief §7.5 changes the same way, from 190 lines to 186. resume-context/SKILL.md gains a blank line and the sentence, reaching 61.
17. `[verified: docs/handoff-nudge.md:38, :63]` The hard block stops the loop before any model call, so no model composes a reply around its text. That is repo prose, not a harness run.
18. `[verified: docs/skills.md:29, one probe, repo prose]` A worktree entered with `EnterWorktree` survives `/clear` (its `CLAUDE.md` reloads), so the in-session route resumes in the same worktree.
    - `[unverified]` beyond that probe. The backstop is the slash skill's own comparison of `SESSION_TARGET:` with the anchored worktree before any `Read` (`resume-context/SKILL.md:27`).
19. `[engineer-verified: "Keep (Recommended)"]` The label selected when `/plan-review` asked whether to keep the consume hook's fenced reload command and its test assertion (M6), which the `Ask:` line does not name.
    - Its "fenced reload command" wording predates the single-backtick shape. Row 36's second label, `"Yes, same shape (Recommended)"`, now covers the shape.
20. `[engineer-verified: "Keep (Recommended)"]` The label selected when asked whether to keep the one fence sentence in `resume-context/SKILL.md` (M6), which the `Ask:` line does not name.
21. `[engineer-verified: "Keep (Recommended)"]` The label selected when asked whether to keep the `HANDOFF_RESUME_ROUTE` nudge sentence, its test and its `docs/handoff-nudge.md` quote (M4), after being told the transcript sample did not confirm the nudge half.
22. `[engineer-verified: "Keep (Recommended)"]` The label selected when asked whether to keep the `brief/SKILL.md` §7.5 change (M3), which the `Ask:` line does not name.
23. `[verified: claude/.claude/settings.json:274, :554, :594, :622; CLAUDE.md "claude/ is stowed into $HOME"]` The nudge, consume and announce hooks are registered by their `~/.claude/hooks/` paths, which stow links into the main checkout. A session in the feature worktree therefore runs today's hooks until merge.
    - `[unverified]` The link's target comes from the platform reviewer's `readlink`, not re-run here.
    - `[verified: claude/.claude/hooks/tests/test_announce_resume_command.py:24; claude/.claude/tests/helpers.py:31-34]` The hook tests run the worktree's own scripts, because `HOOKS_DIR` resolves from the test file's own path.
    - `[verified: .gitignore:12; README.md:548; docs/hooks.md:247]` `.claude/settings.local.json` is gitignored. The repo does not settle which checkout's local file Claude Code reads for a linked-worktree session. README:548 says a local-scope plugin install made from the main checkout is active in linked worktrees. `docs/hooks.md:247` says a main-checkout `false` does not carry into a linked worktree, but it describes one hook's own read of the file.
    - `[verified: docs/scripts.md:315; docs/security-hardening.md:247]` CLI-scope `--settings` already carries per-session settings in this repo, and a `disableAllHooks` passed that way took effect, confirmed by `/hooks`.
    - `[verified: Context "Render probe result"]` A scratch `PostToolUse` hook on `Write` registered for step 0 ran and showed its message on the phone. The Context does not name the route that registered it, so whether the other route works stays `[unverified]`.
24. `[verified: claude/.claude/tests/helpers.py; pyproject.toml:18; claude/.claude/scripts/select-tests.py:461-472, :629, :990-1003; claude-skills/skills/test-conventions/SKILL.md:115]` `code_span_command` goes in `helpers.py`, the shared test-helper location test-conventions §6 names. Its three consumers already import from it (`test_announce_resume_command.py:13`, `test_consume_durable_continuity_file_on_read.py:13`, `test_resume_context_same_session.py:22`). `helpers.py` is a `GLOBAL_TRIGGER_PATHS` entry, and `select-tests.py` reads committed, dirty and untracked changes, so every run from dispatch 2 on selects the full suite.
    - Homes set aside:
      - A new module beside `helpers.py`: `select-tests.py` maps it only to `claude/.claude/tests` (:629), so a later edit to it would skip its importers.
      - The hooks test tree: importing it from the scripts tests needs the hand-kept registries at `select-tests.py:238-285`.
    - `[verified: claude/.claude/tests/test_git_repo_builders.py:8]` That file already self-tests `helpers.py` builders from the same directory, the precedent for the helper's own test file.
25. `[verified: claude/.claude/scripts/resume-context-same-session.sh:214-220, :295-307; claude/.claude/hooks/announce-resume-command.sh:133-137, :178-185]` For a file naming no usable `--cwd`, the script's `SHELL_COMMAND:` is the bare command, while the announce gives a linked session its own root as `--cwd`. The round-trip matrix therefore compares routes on every arm, and commands only where the existing parity test already does (`test_resume_context_same_session.py:1235-1246`).
26. `[verified: claude-skills/skills/resume-context/SKILL.md:31; claude-skills/skills/handoff/SKILL.md:121]` After a `LOAD:`, the slash skill stops when the file names no `--cwd` and its §4 header (§3 in a brief) names another directory, and by then the file is consumed. M3's bare form therefore also requires §4's header to name that directory, or §3 to name no other, so a rule-following file never reaches that stop.
27. `[verified: claude/.claude/scripts/resume-context.sh:268, :334]` `resume-context.sh` rejects a `--cwd` that is not a directory before it moves the file. A step that creates a worktree therefore names the existing directory it runs from.
28. `[verified: claude/.claude/hooks/announce-resume-command.sh:114; claude/.claude/hooks/tests/test_announce_resume_command.py:400-413; docs/skills.md:24]` The announce fires for briefs too, and a brief is for work "the current session is *not* going to do". "Run /clear" fits a handoff, whose session is ending, but would tell the engineer to clear a session that still has work. The brief's `load` wording therefore offers a new session in this worktree first.
29. `[verified: docs/skills.md:28-29]` The slash route loads the file into the conversation, where a later auto-compaction can summarize it, while the shell route keeps it in the system prompt and avoids G3's hook binding. A same-worktree announce no longer shows the shell route.
    - `[engineer-verified: "I’m inclined to accept, yes."]` The engineer's reply when asked whether to accept this trade-off (one announced route per case, with the shell route reachable by hand). The quote is hedged, so the row covers only that acceptance.
30. `[verified: claude/.claude/hooks/nudge-handoff-near-context-cap.sh:714-716, :735-737; claude/.claude/hooks/announce-resume-command.sh:190-198]` Reverting this change is stateless. Nudge log lines and fired markers hold no message text, and the announce writes no file. A continuity file written under M3 stays valid for today's announce, which applies the same verdict function.
31. `[engineer-verified: "Drop the quoting (Recommended)"]` The label selected when asked whether to keep jq `@sh` shell quoting on the consume hook's fenced reload command, which would also quote clean paths and add a space-in-temp-dir test. M6 therefore sets today's command, unchanged and unquoted, in M1's two-line shape, the scope rows 19 and 36 cover.
32. `[engineer-verified: "Stop and ask me (Recommended)"]` The label selected when asked whether, if the render probe shows the fence lines as literal backticks on a surface, the plan should fall back automatically to a fence-free command line. Verification step 0 stopped for the engineer on the phone's result (row 38), and rows 36 and 37 are the engineer's answer.
    - The plan keeps the same posture for M1's two-line shape by its own choice, beyond the question the label answered. A step 0 re-probe showing literal single backticks, a joined line, a cut message, no message, or a shape no easier to copy than its controls stops before dispatch 1 for the engineer. The same result at step 6, after merge, makes it the engineer's decision among keeping the shape, a revert PR (row 30), and a forward fix.
33. `[engineer-verified: "Yes, fair (Recommended)"]` The label selected when asked whether routing a worktree-creating next step by the existing directory the resumed session starts in is a fair reading of the engineer's rule. Row 1's quote is unchanged.
34. `[verified: claude-skills/skills/handoff/SKILL.md:102-103; claude-skills/skills/brief/SKILL.md:66-67, :75-76]` Handoff §3 is "The exact command, file edit, or question to resume on", and for an approved plan it names a `code-writer` dispatch. Brief §6's steps are each "a command, file edit, or decision", and brief §3 lists anchors that include the worktree path. A first step can therefore name no directory, so §7 and §7.5 need a default to keep the lookup deterministic.
35. `[engineer-verified: "Cut step 6 (Recommended)"]` The label selected when asked whether to cut the then-Verification step 6, a pre-merge run of the real hooks, since the render probe and the post-merge check cover it. Verification therefore runs the real hooks only after merge.
36. `[engineer-verified: "I would guess you can’t use fences like the three backs I think the best that you can do is use the single back which at least will make it somewhat easier for me to copy and maybe you can also put it on a new line?"]` The engineer's reply to row 38's result. The hook message drops the three-backtick fence and sets its command in single backticks on a line of its own. The reply is hedged, so the plan reads it as choosing that form for the hook message and nothing wider.
    - `[engineer-verified: "Yes, same shape (Recommended)"]` The label selected when `/plan-review` asked whether the consume hook's reload message (M6) gets the same single-backtick, own-line shape. The reply above concerns the announce, so this label is what covers M6. A fenced consume message would meet row 38's prefix the same way.
37. `[engineer-verified: "And then make sure that when the model does output it in chat that it’s fenced just like the full resume-context command"]` The model's relay in chat keeps the command in a triple-backtick fenced block. Row 36's "fences like the three backs" is the engineer's word for that form, so the relay fence M2 and `resume-context/SKILL.md` prescribe stays as it is.
38. `[verified: the engineer's phone screenshot, seen by the dispatching session in this session]` With a `systemMessage` in the three-backtick shape, the phone client prefixed every line of the message with `PostToolUse:Write says:` and drew it as a warning block. The fence lines showed as stray prefixed fragments around a code block whose one line read `PostToolUse:Write says: /resume-context probe-handoff.md`. The model's own text in the same screenshot rendered an inline code span cleanly. The terminal result is not known.

## Critical files

**Dispatch 1, rule prose, nudge and skill pins** (run first):
- `claude/.claude/hooks/nudge-handoff-near-context-cap.sh`: M4, per "Nudge sentence".
- `claude/.claude/hooks/tests/test_nudge_handoff_near_context_cap.py`:
  - Add a `HANDOFF_RESUME_ROUTE_SENTENCE` literal beside :93-107, and widen the comment at :88-92 to name it.
  - Replace `test_message_leaves_resume_routing_to_the_announce_message` (:2281-2287) with a test that both messages carry the sentence.
  - Assert the soft nudge carries "If you recommend /handoff, tell the user which resume route applies, and run no resume command yourself. " followed directly by the sentence.
  - Assert the hard block's stderr carries "run /handoff now. ", then the sentence, then " Only the user can run /compact.".
  - Add a `route` param to `test_compact_text_constants_hold_no_percent_sign` (:2224-2236), and `route-soft` and `route-hard` params to `test_docs_restate_the_compact_text_the_hook_emits` (:2331-2340).
- `docs/handoff-nudge.md`:
  - After the timing quote (:59-61), add a **Resume-route sentence** label with the constant as a one-line quote. The label marks it as this repo's own rule with no vendor quote, and links to `docs/skills.md`'s `/resume-context` entry for the rule.
  - At :63, replace "Neither message tells the user how to resume after `/handoff`: the announce message after the write gives the route, which differs by worktree." with "Both messages state the resume-route rule, and the announce message after the write gives the exact command and decides the route, because only it reads the written file's own `--cwd`."
- `docs/skills.md`: :25-29, per "Docs".
- `claude-skills/skills/handoff/SKILL.md`: §7 and the §4 phrase, per "Skill text".
- `claude-skills/skills/brief/SKILL.md`: §7.5, per "Skill text".
- `claude-skills/skills/resume-context/SKILL.md`: the one paragraph after :9.
- `claude-skills/skills/tests/test_skills.py`:
  - Add the fence sentence to `_PINNED_RESUME_CONTEXT_CLAUSES` (:5838-5927), and widen its comment (:5836-5837) to cover it.
  - Add a `_PINNED_RESUME_DIRECTORY_CLAUSES` table beside it with four entries, each in full: the first line and the second paragraph of handoff §7, and the same two of brief §7.5. The second paragraph carries the `cd <path> &&` prohibition and the only sentence telling the writer why the directory matters, so a trim at the line cap must fail a test.
  - Its test reads each section with `_raw_heading_section_text` (:5602) and checks each entry with `_assert_pinned_clause_right_bounded` (:5614). The first line's right edge lands on M3's blank line, and the second paragraph's on the section end.
  - The §4 phrase sits mid-line, so its test asserts the whitespace-normalized phrase inside the §4 section instead of a right-bounded clause.
  - Write every new pin before editing the skill text, and run it against the unedited SKILL.md files. Record each pin's failing test ID and its assertion message in the dispatch's return, so the return shows it failed on the clause-absent assertion. Then edit the text and run the pins green.
- The dispatch prompt says each new §7 and §7.5 first line stays one unwrapped line (row 11), because today's §7 and §7.5 text is hard-wrapped and invites copying that style.
- Verification: `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, then `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/`, then `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`, then `wc -l` on the three SKILL.md files.

**Dispatch 2, hook output** (after dispatch 1 returns):
- `claude/.claude/tests/helpers.py`: one `code_span_command(message: str) -> str` beside the continuity helpers (:1284-1304). It is the tests' one definition of M1's two-line shape, which all three consumers below import (row 24). It returns the command.
  - Each clause is its own assertion, with a distinct message that names the clause and includes the offending message. pytest does not rewrite asserts in `helpers.py`, which is not a test module and which nothing passes to `register_assert_rewrite`, so a bare assert there fails with no message.
  - The eight clauses:
    - the message splits on `\n` into exactly two lines;
    - the LEAD is non-empty;
    - the LEAD holds an even number of backticks, so no code span it opens can close on the command line's opening backtick;
    - the second line starts with a backtick;
    - it ends with a backtick;
    - the command between them is non-empty;
    - the command holds no backtick;
    - the command text does not occur in the LEAD.
- `claude/.claude/tests/test_code_span_command.py` (new), beside `test_git_repo_builders.py` (row 24), importing `from helpers import code_span_command`:
  - One well-formed message, its LEAD holding a backtick pair as the linked `load` reason does, returns its command.
  - Each negative breaks exactly one clause and is written `pytest.raises(AssertionError, match=...)` with that clause's name as the pattern, so deleting the clause turns it red. If `match=` is rejected, the dispatch return records a mutation run instead: delete each clause in turn and list which negatives go red.
  - The ten negatives, by the clause each breaks:
    - Line count:
      - a trailing newline;
      - a two-line LEAD;
      - a one-line message.
    - Non-empty LEAD: an empty LEAD.
    - Even LEAD backticks: a LEAD holding one backtick.
    - Opening backtick: a command line with no opening backtick.
    - Closing backtick: a command line with no closing backtick.
    - Non-empty command: an empty command.
    - No backtick in the command: a command holding a backtick.
    - Command absent from the LEAD: a LEAD that carries the command in a backtick pair, as today's mid-sentence shape does.
- `claude/.claude/hooks/announce-resume-command.sh`: M1 and M2, per "Announce wording per branch", its implementation hazards included. Each branch sets LEAD, CMD, ROUTE and PROVENANCE, and one site assembles both channels. Reuse `_lib_resume_context_verdict`, `_lib_continuity_file_resume_cwd` and `_lib_passes_path_char_allowlist` unchanged.
- `claude/.claude/hooks/tests/test_announce_resume_command.py`:
  - The row 15 changes, through `code_span_command`.
  - One test parametrized over nine arms: `load` linked and not linked, each as a handoff and as a brief; `unknown`; and the four mismatch arms. It is the one owner of each arm's LEAD, ROUTE, reason and provenance wording. Every arm asserts:
    - `code_span_command(message)` equals the arm's expected command;
    - its reason appears in the first `systemMessage` line and in `additionalContext`;
    - neither `systemMessage` nor `additionalContext` holds two adjacent backticks, the trace of an empty value inside a backtick pair. Nothing in either channel puts two backticks side by side on purpose, so a plain substring check covers both channels whole, the command line included.
  - The four `load` arms also assert:
    - neither channel holds the written file's absolute path or `--cwd`, so a reworded shell command cannot ride along;
    - the `load` provenance in `additionalContext`, and `_SESSION_MEASURED_PROVENANCE` in neither channel;
    - handoff wording (row 28): "Run /clear, then:" in the first line, "the engineer runs /clear, then" in `additionalContext`, and "new claude session" in neither;
    - brief wording: "Start a new claude session there" before "/clear" in the first line, "starts a new claude session there" in `additionalContext`, and "Run /clear, then:" in neither.
  - Seam-wiring and well-formed-fallback deny cases, one set per allowlist seam (`_lib.sh:325-330`). Each shows that its seam routes the value through the allowlist, and that a rejected value still yields a well-formed message. The single-byte sweep in `test_lib_path_char_allowlist.py` (:71) owns the byte-class matrix, so the docstrings cite it and no further byte classes are added here. Each payload holds one byte outside the allowlist, a newline or a single backtick, and no other:
    - :677 (file path) is parametrized over its newline name and a backtick name, and keeps asserting empty output;
    - :717 (worktree root) is parametrized the same way, keeps its reached markers (:740-741), and asserts that `code_span_command` returns `resume-context {fixture}`;
    - :564 (stubbed file target) gains a newline value and a backtick value beside its semicolon one, and asserts that `code_span_command` returns the command the `expected_command` expression at :557-559 builds for the session kind. The newline sits inside the value, not at its end, because `$(...)` strips trailing newlines (`announce-resume-command.sh:66`), which would leave a usable target and turn the case red for the wrong reason;
    - their clean controls, :669, :750 and :588, stay as they are.
- `claude/.claude/scripts/tests/test_resume_context_same_session.py`:
  - Replace `_announced_slash_argument` (:1189) and `_announced_shell_command` (:1215) with one helper that returns the announced command through `code_span_command`. The tests at :1204 and :1235 keep their assertions.
    - :1204 derives its argument by removing the exact `/resume-context ` prefix from that command. Its equality with `pending.name` stays exact, so the decoy `v1.2-step-3` against `v1.2-step-3.4` still catches a truncated file name.
  - Add a round-trip test parametrized over the verdict table. A linked session reads a file naming this worktree, another worktree, or no `--cwd`. A main-checkout session reads a file naming no `--cwd`, a worktree, or an unreadable one: the `tilde` and `two-disagreeing-commands` entries of `_UNREADABLE_CWD_BODIES` (:72-89) only, because the per-shape cases already run at :743. Each case asserts that the announced route equals the script's outcome, classifying the command by `startswith("/resume-context ")` against `LOAD:` and `startswith("resume-context ")` against `SHELL_COMMAND:`. No command satisfies both prefixes.
  - The same test compares routes only (row 25), and pins the symlinked and dotted spellings as the expected announce-shell, script-load exceptions (row 6).
- `claude/.claude/hooks/consume-durable-continuity-file-on-read.sh`: M6. The `systemMessage` at :141 becomes `Continuity file moved to {dest}. Reload with:<NL><TICK>claude --append-system-prompt-file {dest}<TICK>`, the command text unchanged (row 31). Its jq filter is already single-quoted in the shell, so the backticks need no escape there. `additionalContext` is unchanged.
- `claude/.claude/hooks/tests/test_consume_durable_continuity_file_on_read.py`: beside :133 and :418, assert that `code_span_command(payload["systemMessage"])` equals `f"claude --append-system-prompt-file {dest}"`.
- `docs/hooks.md`: the edits under "Header and `docs/hooks.md`".
- Verification: `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck` first, per the implementation hazards, then `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`, which selects the full suite here (row 24), then `scripts/venv-run.sh ruff check claude/.claude/`.
- The dispatch prompt tells the code-writer to run `select-tests.py` in the background when a foreground run would reach the Bash tool's time limit, and to report which suite it selected. It also says to reproduce at the merge-base any failure in a file outside this dispatch's set before touching that file, per CLAUDE.md's "Prove your change caused a failing check" rule.

**Dispatch rules**, the start gate first:
- Dispatch 1 starts only after Verification step 0 ends in an outcome that builds M1 as written.
- Dispatch 2 starts only after dispatch 1 returns. Dispatch 1 is safe alone in behavior, because today's announce reads a file written under the new §7 rule through the same verdict and still shows both routes. Dispatch 2's single-route announce is right only once that rule exists (row 7).
- The two never run in parallel. They share the feature worktree, and dispatch 2's `helpers.py` edit makes `select-tests.py` run the full suite, which would test the other dispatch's half-finished edits.
- Both ship in one PR, with nothing committed or pushed between them, so `main` never holds one half.
- If dispatch 2 cannot land, the PR is held, not split, because dispatch 1's `docs/skills.md` Announce bullet describes dispatch 2's announce.

`.claude/plans/resume-route-deterministic.md` is committed at `/plan-it` Step 7.

## Verification

0. Render probe, by the engineer, before dispatch 1. Only this step shows, before any code is written, how a client renders the `systemMessage` shape M1 builds. It needs no code from this plan.
   - **Done: the phone, in the three-backtick shape.** A scratch `PostToolUse` hook on `Write` ran, and the phone showed what row 38 records. The step stopped for the engineer (row 32), and this revision builds the answer, rows 36 and 37. The terminal result was not recorded.
   - **Remaining: M1's two-line shape against two controls, on the phone and in the terminal.** Edit the scratch probe in `/tmp/render-probe/` so it reads the payload's `.tool_input.file_path` and picks its `systemMessage` by the written file's base name. It emits six variants, three shapes of each of two commands, so the engineer judges "easier than today" in one round:
     - The two commands: `/resume-context probe-handoff.md` under the handoff `load` linked LEAD, and a shell command of realistic length, `resume-context --cwd <dir> <file>` with two scratch absolute paths of about 60 characters each, under the shell LEAD with the linked, usable-target mismatch reason. Each LEAD takes scratch paths for its backticked values.
     - The plan's shape: the LEAD, a newline, then the command between single backticks.
     - The same-line variant: the LEAD and the backticked command on one line.
     - The control, today's mid-sentence single-backtick shape: the backticked command inside a sentence, as `announce-resume-command.sh:186` prints the shell command today.
   - Pipe each variant's payload, `{"tool_input":{"file_path":"<that variant's file>"}}`, into the script by hand, and check the message itself, not only that the output is valid JSON. A backtick the script fails to escape drops the command, yet the output stays valid JSON and the log still gains a line.
     - Each plan-shape `systemMessage` holds exactly one newline. Its second line, which `jq -r .systemMessage | sed -n 2p` prints, starts and ends with a backtick, and the text between them is the command.
     - Each other variant holds no newline, and holds the command between backticks.
   - Truncate `probe.log` after the hand-run and before registration. A non-empty log at the first read after that means this registration fired.
   - Register the probe as a `PostToolUse` hook with matcher `Write`, through the route that ran it before: the feature worktree's gitignored `.claude/settings.local.json`, or a scratch settings file passed to a new session with `claude --settings <file>` (row 23). Start a new session in the feature worktree, have it `Write` the six variant files under `/tmp/render-probe/` in turn, and read each message on the phone and in the terminal. When the log is empty at the first read, check with `/hooks` whether the registration is listed (`README.md:113`) before trying the other route. Afterwards remove the registration, and confirm with `/hooks` that it is gone.
   - A surface passes when, for both commands, the plan's shape shows the LEAD and the command on separate lines, and the engineer selects the command in one gesture, without the client's line prefix and without literal backticks. On the terminal, and for the shell form on either surface, the engineer also pastes the command where it is used, without running it, and it parses as intended: the bare command, with no prefix and no backtick.
   - Judge each surface on its own. Dispatch 1 starts only on an outcome below that builds M1 as written:
     - **Both surfaces pass:** build M1 and M6 as written.
     - **A surface shows literal single backticks, joins the two lines, cuts the message short, or shows no message, or the engineer finds the plan's shape no easier to copy than the control or the same-line variant:** stop and bring the result to the engineer (row 32). No outcome changes the shape on its own.
     - **Neither route logs a line any more:** build M1 as written. The two-line shape and the terminal are first observed at step 6, after merge, where a bad render is a post-merge decision for the engineer (row 32).
1. After each dispatch and again at the end, run `scripts/venv-run.sh python3 claude/.claude/scripts/select-tests.py`. From dispatch 2 on, it selects the full suite, because `helpers.py` is a `GLOBAL_TRIGGER_PATHS` entry (row 24). That is CLAUDE.md's first full-suite case, not a run widened by hand. `test_resume_command_template_contract.py` must pass with no edit (row 11).
2. Run `scripts/venv-run.sh ruff check claude/.claude/ claude-skills/` and `scripts/list-shell-files.sh | xargs -0 scripts/venv-run.sh shellcheck`.
3. `wc -l claude-skills/skills/handoff/SKILL.md claude-skills/skills/brief/SKILL.md claude-skills/skills/resume-context/SKILL.md` prints at most 200 for each file, the cap `check-skill-length.sh:140` enforces at commit. Row 16 expects 196, 186 and 61.
4. Run `/code-review`. It dispatches `/skill-review` for the three SKILL.md files, which `require-skill-review.sh` enforces before commit.
5. `git grep -n -e 'Resume in a new process' -e 'Resume (run in your shell)' -e 'run none of them' -e 'in a new session started here' -- claude/ docs/` prints nothing. `git grep -n '/resume-context' claude/.claude/hooks/nudge-handoff-near-context-cap.sh` matches only the `HANDOFF_RESUME_ROUTE` line.
6. After merge, by the engineer, with only the new hooks live. Only this step observes the real flow: files written by a model following the shipped §7 and §7.5, their announces on both surfaces, the model's relay, and the consume hook's reload command. It runs after merge, so it can stop nothing (rows 32, 35).
   - Pick `probe-` slugs, and confirm in a terminal that no pending file under `<config-dir>/handoffs/` or `<config-dir>/briefs/` uses one, because a `Write` to an existing path overwrites it.
   - Have a model following the shipped skills write four handoffs and one brief, from four sessions:
     1. a handoff from a linked-worktree session whose next step runs in that worktree, with §3 a question or a `code-writer` dispatch, expecting the default `--cwd` naming that root (row 34) and the slash route;
     2. a handoff from a linked-worktree session whose next step runs in another worktree or the main checkout, expecting a `--cwd` naming that directory and the shell route;
     3. a handoff from a main-checkout session whose next step runs in a linked worktree, expecting a `--cwd` naming that worktree and the shell route;
     4. a handoff from an `EnterWorktree`-entered session, expecting the route the lookup gives for the file's `--cwd`;
     5. a brief from session 1 whose first step is a decision and whose §3 names another worktree, expecting a `--cwd` naming that worktree and the shell route.
   - Read each file's resume command with `grep` in a terminal, never with a `Read`, because a `Read` consumes the file. Then run four checks per file, in order:
     1. the `--cwd` names the expected directory;
     2. the announce names the expected route, with its command alone on its own line as code, on the terminal and on the phone;
     3. the model's relay fences that one command and names no other route;
     4. the command copies and pastes as step 0's pass defines, on each surface.
   - After file 1's checks, run `/clear` on the phone, and check whether the announce and the relay stay in view.
   - Have a session `Read` one throwaway continuity file, and check that the reload command sits alone on its own line as code.
   - Delete leftover probe files with `rm` in a terminal.
   - Outcomes:
     - A hook output fault, meaning an empty or malformed message or a route the lookup does not give for the file's `--cwd`: revert the merge in a new PR, or fix forward. The revert is stateless (row 30).
     - A surface that shows literal single backticks, or joins the command's line to the LEAD: bring it to the engineer, who decides among keeping the shape, a revert PR and a forward fix (row 32).
     - An announce that `/clear` removes from view on the phone: the handoff `load` LEAD changes to put the copy first, and the nine-arm test's pinned "Run /clear, then:" changes with it.
     - A `--cwd` naming the wrong directory, or a relay that is unfenced or names a second route, is model variance, not a revert trigger. Report each to the engineer as input to the follow-up that Out of scope names.
   - This step is the only check of whether models now route correctly. The tests defend wording and structure (M7).

## Out of scope

- **Canonicalizing the file's `--cwd` (a symlink or `..` spelling) in the announce:** a documented known gap (`announce-resume-command.sh:89-91`, `docs/hooks.md:199`), unrelated to which route is named.
- **`_lib.sh`, which is outside this change's files:** `_lib_resume_context_verdict` itself, for example loading a file whose `--cwd` names this main checkout's own path, where M3 writes the bare form instead. Also the verdict's comment (`_lib.sh:5404-5407`), which says both inputs arrive canonicalized though the announce compares raw (row 6).
- **The announce's fallback** that gives a linked session its own `--cwd` when the file names no usable directory (:178-185): M3 makes a linked session always write a `--cwd`, so only a hand-written or malformed file reaches it.
- **The slash skill's header check** (`resume-context/SKILL.md:31`): M3's bare-form conditions keep a rule-following file from reaching it (row 26).
- **`README.md:507`, knowingly left:** it keys the resume form on what the file names and gives the shell command first, coarser than M3's rule. Both routes it names still work, and its last sentence defers to `docs/skills.md` for which applies. The :515 table row already says the announce names the route.
- **`docs/cost-levers-considered.md:333-343`:** a dated record, read-only under CLAUDE.md's Scope discipline Axis 3. It stays true, because the route sentence makes no cost claim.
- **The consume hook's `additionalContext`:** it gives the model no command to relay.
- **A consume destination holding a space, a newline or a backtick:** it would come from a `TMPDIR` the session's own user sets, not from content others write (`resume-context.sh:164-165`). A space splits the pasted reload command, and no quoting or allowlist is added (row 31).
- **A live nudge check:** a session forced to a nudge by lowering `HANDOFF_NUDGE_ABS_CAP` writes a `nudged` line that records no override (`nudge-handoff-near-context-cap.sh:570`, :715, :736). `handoff_signal_response.py`, `rearm_backtest.py` and `spend_over_threshold.py` read that log, and no `transcript-analysis.py` subcommand takes a session exclusion, so such a session would skew later measurements. The nudge tests pin the sentence in both messages.
- **A measurement of whether models now route and fence correctly, which needs a follow-up issue:** no existing command reads the relay after a handoff write. `transcript-analysis.py handoff-signal-response` audits whether a same-session `/handoff` followed a context-budget signal, and the spend after it (`handoff_signal_response.py:1-3`, :331). Its `--sample --context-turns N` cards attach the assistant text after each signal, so they can read the nudge half. The engineer files that issue before the PR is marked ready, and the PR body carries its number.
- **A worktree-change clause in the shell LEAD for the cross-worktree arm:** the shell LEAD's "cannot confirm" wording serves the `unknown` and main-checkout arms too, and step 6 observes how the designed cross-worktree case reads before any wording change.
