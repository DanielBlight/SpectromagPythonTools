"""Compatibility import; implementation lives in cli.inspect_pgm1se."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.cli.inspect_pgm1se")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
