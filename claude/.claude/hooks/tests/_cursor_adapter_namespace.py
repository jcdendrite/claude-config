"""One object over cursor-adapter.py and the modules of its cursor_adapter package, for the tests that read and patch them.

A module that imports a name by name holds its own binding of it, so patching the defining module alone reaches no importer.
A read through the namespace returns the object every binding of the name holds, and a write rebinds each of them.
A namespace holds references to the modules its own load registered and never looks them up in `sys.modules`, so it keeps
addressing its own load after a later load replaces the registered modules.
"""

from __future__ import annotations

import sys
import types
from typing import Any

# Equal to cursor-adapter.py's ADAPTER_PACKAGE_NAME, which `AdapterNamespace.over_load` asserts.
ADAPTER_PACKAGE_NAME = "cursor_adapter"


class AdapterNamespace(types.ModuleType):
    """Reads and writes a name through every module in `spanned_modules` that binds it."""

    def __init__(self, name: str, modules: list[types.ModuleType], file: str) -> None:
        super().__init__(name)
        self.__dict__["__file__"] = file
        self.__dict__["_spanned_modules"] = tuple(modules)

    @classmethod
    def over_load(cls, entry: types.ModuleType) -> AdapterNamespace:
        """The namespace over `entry` and each `cursor_adapter.<name>` module in `sys.modules`, put there by the caller's load."""
        assert entry.ADAPTER_PACKAGE_NAME == ADAPTER_PACKAGE_NAME, "the entry and the namespace name different packages"
        submodules = [
            module
            for module_name, module in sys.modules.items()
            if module_name.partition(".")[0] == ADAPTER_PACKAGE_NAME and "." in module_name
        ]
        return cls(entry.__name__, [entry, *submodules], entry.__file__)

    @property
    def spanned_modules(self) -> tuple[types.ModuleType, ...]:
        return self.__dict__["_spanned_modules"]

    def _binding_modules(self, name: str) -> list[types.ModuleType]:
        """The modules that bind `name`. Raises AttributeError when none does or two bind different objects."""
        binding_modules = [module for module in self.spanned_modules if name in module.__dict__]
        if not binding_modules:
            raise AttributeError(f"no module of the adapter binds {name!r}")
        current = binding_modules[0].__dict__[name]
        if any(module.__dict__[name] is not current for module in binding_modules[1:]):
            raise AttributeError(f"the adapter's modules bind {name!r} to different objects")
        return binding_modules

    def __getattr__(self, name: str) -> Any:
        return self._binding_modules(name)[0].__dict__[name]

    def __setattr__(self, name: str, value: Any) -> None:
        for module in self._binding_modules(name):
            module.__dict__[name] = value
