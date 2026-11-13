"""Compatibility import; implementation lives in cli.fit_pgm1se_conversion."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.cli.fit_pgm1se_conversion")

if __name__ == "__main__":
    _implementation.main()
else:
    sys.modules[__name__] = _implementation
