"""transcript-analysis.py's decomposed modules; docs/transcript-analysis-architecture.md says what each one holds.

session_pricing is the one module with a stability promise, for importers outside this repository.
scope.py's docstring states the import discipline its reassignable PROJECTS_DIR requires.
transcript-analysis.py itself is a plain top-level script outside this package; it re-exports cli's
build_parser and main.
"""
