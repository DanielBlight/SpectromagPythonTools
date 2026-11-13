"""Compatibility import; implementation lives in cli.map_timing."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.cli.map_timing")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
