---
paths:
  - "**/*.py"
  - "**/requirements*.txt"
  - "**/pyproject.toml"
  - "**/Pipfile"
---

## Python environment conventions

PEP 668, the CPython `venv` reference, and the PyPA Python Packaging User
Guide ground the interpreter-targeting and detection guidance below. 
Applies when installing or restoring Python dependencies.

- **Never install into the machine's system interpreter; name the project
  environment's interpreter explicitly in every command.** Activation
  (`source .venv/bin/activate`) doesn't survive across separate Bash tool
  calls, so an explicit path is the only reliable form.
- **Detect before creating, in this order:**
  1. The tool the repo already declares: `uv sync` (`uv.lock`), `poetry
     install` (Poetry `pyproject.toml`), `pipenv install` (`Pipfile`), or the
     conda env from `environment.yml`.
  2. A repo can declare a tool and still carry a bare `.venv/`. Check the
     declared tool first — it still owns that directory even when a bare
     `.venv/` is also present.
  3. Only then an existing `$VIRTUAL_ENV` or `.venv/`/`venv/`.
- **`poetry install` and `pipenv install` above restore already-declared
  dependencies; their sibling fetch verbs don't.** `poetry add` and
  `pipenv install <pkg>` aren't gated against a network fetch (GH-872).
  Route a new-package install through the normal naming+confirmation
  discipline instead.
- **Nothing declared → `python3 -m venv .venv`, then `.venv/bin/pip install
  -r requirements.txt`** (or `.venv/bin/pip install -e .` for a
  `pyproject.toml`-only project). `.venv` is the name to use — it's the
  conventionally gitignored one.
- **`<venv>/bin/pip`, not `<venv>/bin/python -m pip`.** `python -m pip`
  exists to pin an ambiguous, PATH-resolved `pip` to a specific
  interpreter; an explicit `<venv>/bin/pip` path is already interpreter-
  pinned by construction, so `-m` buys nothing there.
- **`python3 -m venv` can exit 0 and produce no pip** where the distro
  splits `python3-venv`/`ensurepip` into a separate package. Probe
  `python3 -c "import ensurepip"` first — `install-dev.sh:44-75` in the
  claude-config repo is the worked guard.
- **An "externally managed environment" error (PEP 668) means the command
  is pointed at the system interpreter.** Re-point it at the project
  environment instead of reaching for `--break-system-packages`.
- **Carve-out:** an ephemeral single-project environment — a CI runner's
  `setup-python`, a container built for one project — already is the
  project environment. This rule targets a developer machine's shared
  system interpreter, not those.
