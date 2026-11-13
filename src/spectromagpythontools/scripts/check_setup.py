"""Compatibility import; implementation lives in cli.check_setup."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.cli.check_setup")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
