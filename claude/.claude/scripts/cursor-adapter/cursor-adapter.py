#!/usr/bin/env python3
"""Generates, installs, checks and removes the Cursor adapter's user-level hook entries.

Usage: cursor-adapter.py generate [--print-entry REGISTRATION --shim PATH] [--python PATH]
       cursor-adapter.py install --config-dir DIR [--acceptance-only]
       cursor-adapter.py uninstall
       cursor-adapter.py check [--hook-env]
       cursor-adapter.py status
       cursor-adapter.py self-check --wrapper PATH

`install` and `uninstall` change ~/.cursor/hooks.json and the files beside it. docs/cursor-adapter.md describes the
installer, the Recovery steps every shim deny points to, and the limits. Its code is the `cursor_adapter` package beside its
real path, which imports only the standard library, so the recorded python3 can run it under `-I -S`. Loading it runs no
step.
"""

import importlib
import importlib.machinery
import importlib.util
import os
import sys

# `-I` keeps this file's directory off sys.path, so the package is found by searching that directory alone.
ADAPTER_PACKAGE_NAME = "cursor_adapter"


def import_adapter_cli():
    adapter_dir = os.path.dirname(os.path.realpath(__file__))
    spec = importlib.machinery.PathFinder.find_spec(ADAPTER_PACKAGE_NAME, [adapter_dir])
    # A directory without a regular __init__.py gets a namespace spec, whose origin is None.
    if spec is None or spec.origin is None:
        raise ImportError(f"no {ADAPTER_PACKAGE_NAME} package in {adapter_dir}", name=ADAPTER_PACKAGE_NAME)
    package = importlib.util.module_from_spec(spec)
    sys.modules[ADAPTER_PACKAGE_NAME] = package
    previous_dont_write_bytecode = sys.dont_write_bytecode
    # The adapter writes no bytecode into the checkout, and a test process keeps its own setting.
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(package)
        return importlib.import_module(f"{ADAPTER_PACKAGE_NAME}.cli")
    finally:
        sys.dont_write_bytecode = previous_dont_write_bytecode


adapter_cli = import_adapter_cli()

if __name__ == "__main__":
    sys.exit(adapter_cli.main())
