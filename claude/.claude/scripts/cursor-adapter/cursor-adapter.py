#!/usr/bin/env python3
"""Generates, installs, checks and removes the Cursor adapter's user-level hook entries.

Usage: cursor-adapter.py generate [--print-entry REGISTRATION --shim PATH] [--python PATH]
       cursor-adapter.py install --config-dir DIR [--acceptance-only]
       cursor-adapter.py uninstall
       cursor-adapter.py check [--hook-env]
       cursor-adapter.py status
       cursor-adapter.py self-check --wrapper PATH

`install` and `uninstall` change ~/.cursor/hooks.json and the files beside it. docs/cursor-adapter.md describes the
installer, the Recovery steps every shim deny points to, and the limits. The module imports only the standard library, so
the recorded python3 can run it under `-I -S`. Loading it runs no step.
"""

import argparse
import contextlib
import errno
import fcntl
import functools
import hashlib
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import traceback
import types

# --- the parts ---

# Each part executes into this module's namespace, so every name, and a test's patch of one, resolves as in one file.
# The order is file order: a part's top-level statements read only names the entry or an earlier part defines.
ADAPTER_PART_FILE_NAMES = (
    "cursor-adapter-core.py",
    "cursor-adapter-hooks-json.py",
    "cursor-adapter-tools.py",
    "cursor-adapter-install.py",
    "cursor-adapter-sandbox.py",
    "cursor-adapter-uninstall.py",
    "cursor-adapter-check.py",
)


def adapter_directory():
    return os.path.dirname(os.path.realpath(__file__))


def execute_adapter_parts():
    for part_file_name in ADAPTER_PART_FILE_NAMES:
        part_path = os.path.join(adapter_directory(), part_file_name)
        with open(part_path, "rb") as part_file:
            source = part_file.read()
        exec(compile(source, part_path, "exec"), globals())  # the parts are this module's own code


execute_adapter_parts()


# --- generate ---


def run_generate(arguments):
    load_shim_or_refuse("generate", GENERATE_RESTORE_SENTENCE)
    python_path = arguments.python or (record_or_none() or {"tool_paths": {}})["tool_paths"].get("python3") or sys.executable
    if arguments.print_entry:
        if not arguments.shim:
            raise AdapterError("--print-entry needs --shim PATH.")
        emitted = generate_entries(python_path=python_path, shim_path=arguments.shim, only_key=arguments.print_entry)
    else:
        emitted = generate_entries(python_path=python_path)
    sys.stdout.write(json_bytes({"version": HOOKS_JSON_VERSION, "hooks": hooks_object(emitted)}).decode("ascii"))
    return 0


# --- command line ---


def build_parser():
    parser = argparse.ArgumentParser(prog=ADAPTER_FILE_NAME, allow_abbrev=False)
    commands = parser.add_subparsers(dest="command", required=True)
    generate = commands.add_parser("generate", allow_abbrev=False)
    generate.add_argument("--print-entry")
    generate.add_argument("--shim")
    generate.add_argument("--python")
    install = commands.add_parser("install", allow_abbrev=False)
    install.add_argument("--config-dir", required=True)
    install.add_argument("--acceptance-only", action="store_true")
    commands.add_parser("uninstall", allow_abbrev=False)
    check = commands.add_parser("check", allow_abbrev=False)
    check.add_argument("--hook-env", action="store_true")
    commands.add_parser("status", allow_abbrev=False)
    self_check = commands.add_parser(SELF_CHECK_SUBCOMMAND, allow_abbrev=False)
    self_check.add_argument("--wrapper", required=True)
    return parser


def main(argv=None):
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "generate":
            return run_generate(arguments)
        if arguments.command == "install":
            return run_install(arguments)
        if arguments.command == "uninstall":
            return run_uninstall(arguments)
        if arguments.command == "check":
            return run_hook_env_check() if arguments.hook_env else run_check(arguments)
        if arguments.command == "status":
            return run_status(arguments)
        return run_self_check_subcommand(arguments.wrapper)
    except PartialWriteError as error:
        print(f"failed after writes began: {error}", file=sys.stderr)
        return PARTIAL_WRITE_EXIT_STATUS
    except AdapterError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 1
    except Exception as error:
        frame = traceback.extract_tb(error.__traceback__)[-1]
        located = f"{type(error).__name__} at {os.path.basename(frame.filename)}:{frame.lineno} in {frame.name}"
        nothing_written = " Nothing was written." if arguments.command in ("install", "uninstall") else ""
        print(f"`{arguments.command}` failed on an unexpected {located}.{nothing_written}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
