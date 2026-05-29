# Fix contributor `.venv` setup friction with a bootstrap script

## Context

**Goal:** give contributors and agents one robust, self-diagnosing command to create the
repo's dev `.venv`, so the silent pip-less-venv trap stops recurring.

The repo-root contributor `.venv` is broken: it contains only python symlinks — no `pip`,
`pytest`, `ruff`, or `pyyaml` (`ls .venv/bin/` → `python`, `python3`, `python3.12` only).
A session burned ~3 minutes fumbling between `python3`, `.venv/bin/python3`, and
`.venv/bin/pip` trying to run `validate_skill_structure.py` (which `import yaml`s).

Confirmed root cause on this machine (read-only diagnostics):
- System `python3` (3.12.3) has **no `ensurepip`** (`ModuleNotFoundError`), and **no
  `python3-venv` apt package** is installed.
- Therefore `python3 -m venv .venv` (the command in `CLAUDE.md:11` / `README.md:337`)
  exits 0 but produces **no pip** — the exact broken state observed. A later
  `python3 -m venv --upgrade` (per `.venv/pyvenv.cfg`) can't add pip either.
- The fix drafted in the session — `python3 -m venv .venv --upgrade-deps` — **does not
  work**: `--upgrade-deps` itself calls `ensurepip`, which is absent. The only real
  unblock is `sudo apt install python3.12-venv` first (user-run; never sudo from an agent).
- System `python3` already has `yaml 6.0.1`, `pytest 9.0.3`, `ruff 0.6.9` — which is why
  the session's final `import yaml` worked. But system `pytest` is **9.x** while
  `requirements-dev.txt` pins `pytest==8.*`, so bare `pytest` drifts from CI. The `.venv`
  exists precisely to pin CI-parity versions; "just use system python" is not a fix.

This is **two separate venvs** — only the contributor one is broken:
- *Contributor* `.venv` (manual, repo root) — for `pytest`/`ruff`. **Broken.**
- *Plugin* runtime venv (`${CLAUDE_PLUGIN_DATA}/venv`, auto-provisioned by the
  `provision-validator-venv.sh` SessionStart hook for `validate_skill_structure.py`) —
  a different thing, and **resilient**: `require-skill-review.sh:87-90` falls back to
  system `python3` when that venv is absent. Out of scope here (see below).

The correct recovery *is* already documented — but only as prose at `README.md:343`, not
in the `CLAUDE.md` "Commands" block an agent reads first, and never as an executable step,
so it never runs automatically. The raw create+install sequence is also duplicated in
**four** places (`CLAUDE.md:11-12`, `README.md:337-338`, `install.sh:96`,
`CONTRIBUTING.md` pointer), which drifts.

## Approach

Add an idempotent, self-diagnosing **`install-dev.sh`** at the repo root (sibling to
`install.sh`; a contributor dev tool, *not* stowed to `~/.claude/`), and make it the single
source of truth that the four doc sites reference instead of restating the raw commands.

Name rationale: `-dev` is the cross-ecosystem token for development/contributor extras and
already means exactly that *in this repo* (`requirements-dev.txt`) — the script that installs
`requirements-dev.txt` carries the same suffix (one vocabulary), shares the `install` stem
with `install.sh` so the pair reads and sorts together. The one gap — `-dev` doesn't itself
say "not for end users" — is closed in prose: each doc reference gets a short clause noting
the script is for contributors and builds the test `.venv` from `requirements-dev.txt`.

Script behavior (`set -euo pipefail`; all expansions quoted):
1. **Anchor CWD before any destructive op.** Refuse to run unless `requirements-dev.txt`
   exists in CWD (proves main worktree root — the script needs it in step 4 anyway) and
   `.venv` is not a symlink (`[ -L .venv ]` → refuse). This guards the `rm` in step 3 from
   running against the wrong directory.
2. **Guard ensurepip first** — reuse the `install.sh:88` shape `if ! python3 -c "import
   ensurepip"`, printing the message and `exit 1` explicitly (not relying on `set -e`, which
   would suppress the guidance). The message must be **distro-aware**: gate the apt line
   behind Debian detection (`command -v apt-get` or `[ -f /etc/debian_version ]`) and emit
   the version-matched `sudo apt install python3.X-venv` (X parsed from a `python3 --version`
   command-substitution into a quoted var, validated non-empty); on non-Debian emit the
   distro-agnostic phrasing already used at `provision-validator-venv.sh:70` ("install your
   platform's Python venv support, e.g. `python3-venv` on Debian/Ubuntu"). Never assert
   `apt` on a non-Debian box. Do **not** attempt sudo.
3. **Heal an unhealthy `.venv`.** Treat the existing `.venv` as healthy only if it passes
   the *same* probe as step 4's success criterion (see below) — not the weaker
   `.venv/bin/pip` existence check, which would let a partial install (pip present, deps
   missing) stick across re-runs. If absent or unhealthy: `rm -rf .venv` (now CWD- and
   symlink-anchored by step 1) then `python3 -m venv .venv` →
   `.venv/bin/pip install -r requirements-dev.txt`.
4. **Verify (= the health probe of step 3).** `.venv/bin/python -c "import yaml, pytest"`
   and `.venv/bin/ruff --version`; print a one-line success with the pinned versions.
   Non-zero exit on any failure so the trap can never again pass silently.
5. Operate on `.venv` in the current directory; must run from the **main** worktree root
   (the `.venv` lives there only — `README.md:345`), which step 1's anchor enforces.

Then **DRY-collapse** the duplicated sequence: each doc site references `./install-dev.sh`
as the canonical setup step and keeps only its site-specific note (worktree-relative
invocation, CI parity). The Debian explanation moves into the script's runtime output —
one authoritative home.

Rationale: the create+install sequence is already duplicated 4× and the recovery is
prose-only; a script that *becomes* the canonical home is the DRY-correct consolidation,
and it is the only option that makes the Debian recovery executable and fails loudly.

**Do not share a helper with `provision-validator-venv.sh`.** It has the opposite contract
— best-effort, always-`exit 0`, stderr-suppressed, SessionStart-frequency — whereas this
script is fail-loud, non-zero-on-error, run-once-by-a-human. A shared helper would have to
parameterize exit policy, banner visibility, and CWD target: more coupling than the ~6
shared lines justify. *Copy* its distro-agnostic failure phrasing (`:70`); don't abstract.

### Lighter alternatives considered
- **Docs-only** (surface the caveat + a `.venv/bin/pip` existence check in the `CLAUDE.md`
  Commands block): adds a 5th prose copy and leaves recovery as manual sequencing
  (delete → apt → recreate → install) — does not fail loudly and does not stop the drift.
- **`install.sh` auto-provisions the `.venv`**: changes `install.sh`'s deliberate contract
  (it does not touch Python today — `README.md:182`) and slows the common install path for
  users who never run tests. The script keeps provisioning opt-in.

## Critical files

- **Create** `install-dev.sh` (repo root, executable). Reuse the ensurepip-check idiom
  from `install.sh:88-92`.
- **Modify** `CLAUDE.md:11-14` — Commands block: replace the `python3 -m venv` +
  `pip install` two-liner with `./install-dev.sh`; keep the `.venv/bin/pytest` and
  `.venv/bin/ruff` run lines.
- **Modify** `README.md:336-343` — Tests section: reference `./install-dev.sh` for setup;
  keep the worktree note (`:345`) and CI-parity note (`:347`); drop the now-redundant prose
  Debian paragraph (logic lives in the script).
- **Modify** `install.sh:94-97` — point the "Optional" footer at `./install-dev.sh`.
- **Verify** `CONTRIBUTING.md:33` still reads correctly (already a README pointer; likely no
  change).
- **Modify** `.github/workflows/tests.yml` path filter (line ~58) to add the repo-root
  `install-dev.sh` path. Without this, a future edit to the root-level script does **not**
  match the existing filter (`claude/.claude/(hooks|skills|scripts|tests)/`,
  `plugins/skill-management/`, the workflow, `pyproject.toml`) and would ship untested.
- **Create** `claude/.claude/scripts/tests/test_setup_dev_venv.py` — reuse the **PATH-stub
  mechanism** of `claude/.claude/hooks/tests/test_provision_validator_venv.py`, but **not its
  assertion shape**: that precedent asserts `exit 0` + banner suppression (a best-effort
  hook), whereas this script's contract is fail-loud (exit non-zero + emit guidance). Cover:
  (a) ensurepip-missing → stub `python3 -m venv` to fail (mirroring precedent lines 73-77),
  assert `returncode != 0` and the derived apt-package string in stderr; (b) existing
  pip-less `.venv` → assert it is recreated and the `rm` targets only the venv dir (mirroring
  the precedent's reprovision/cleanup tests); (c) happy path via stubbed `python3`/`pip`/`ruff`
  (no live network install) asserting the success line + that the import/`ruff` probes ran;
  (d) apt-name derivation — stub `python3 --version` and assert the exact package token.
  Resolve the repo-root script path via `Path(__file__).parents[N]` — do **not** copy the
  precedent's `from helpers import …` (resolvable only via the `claude/.claude/tests`
  `pythonpath` entry; it would `ModuleNotFoundError` from `scripts/tests/`). Copy the real
  `requirements-dev.txt` into any fixture that exercises the install step, not a synthetic
  stub. Path is under `claude/.claude/` so CI's `tests.yml` filter triggers on test edits.

## Verification

1. **Broken-state heal (the actual bug):** with the current pip-less `.venv` present, run
   `./install-dev.sh`. Expect: it detects no ensurepip → prints
   `sudo apt install python3.12-venv` and exits non-zero (it must not silently "succeed").
2. After `sudo apt install python3.12-venv` (user-run), re-run `./install-dev.sh`. Expect:
   `.venv/bin/pip`, `pytest`, `ruff` exist; success line prints pinned versions
   (`pytest 8.x`, not system 9.x).
3. `.venv/bin/pytest claude/.claude/` and `.venv/bin/ruff check claude/.claude/` pass.
4. `.venv/bin/python -c "import validate_skill_structure"` (with
   `plugins/skill-management/scripts` on path via `pyproject.toml`) imports cleanly — the
   action the original session was blocked on.
5. New test: `.venv/bin/pytest claude/.claude/scripts/tests/test_setup_dev_venv.py` — all
   four branches (a)–(d) pass, including the recreate-pip-less-`.venv` `rm`-target assertion.
6. `git grep -n "python3 -m venv .venv"` returns only `install-dev.sh` (and any
   intentional README explanation) — confirms the DRY collapse left one authoritative home.
7. Non-Debian message check: with `apt-get` absent / `/etc/debian_version` removed (via the
   test's PATH+env stub), the failure branch emits the distro-agnostic phrasing, not `apt`.

**Immediate recovery (outside this plan / run from the repo root):**
```bash
sudo apt install python3.12-venv          # supplies ensurepip
rm -rf .venv
./install-dev.sh
```

## Out of scope

- **Plugin provisioning hook** (`provision-validator-venv.sh`) hits the same ensurepip gap
  on this box, but `require-skill-review.sh` already falls back to system `python3` (which
  has `yaml`), so the commit-time path is resilient. Optional later hardening: have that hook
  emit the same apt guidance on ensurepip failure — not needed to unblock this.
- Changing CI (`tests.yml` uses `setup-python` + system `pip install`, no `.venv`) — already
  correct and shares the same `requirements-dev.txt` pins.
- Stowing the script to `~/.claude/` — it is repo-contributor tooling, not user config.
