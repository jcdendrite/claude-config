"""The `generate` command and the command line."""

import argparse
import os
import sys
import traceback

from .check import run_check, run_hook_env_check, run_status
from .core import (
    ADAPTER_FILE_NAME,
    GENERATE_RESTORE_SENTENCE,
    HOOKS_JSON_VERSION,
    PARTIAL_WRITE_EXIT_STATUS,
    SELF_CHECK_SUBCOMMAND,
    AdapterError,
    PartialWriteError,
    json_bytes,
)
from .hooks_json import generate_entries, hooks_object
from .install import load_shim_or_refuse, run_install
from .tools import record_or_none, run_self_check_subcommand
from .uninstall import run_uninstall

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
