"""Compatibility import; implementation lives in cli.commissioning_gui."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.cli.commissioning_gui")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
