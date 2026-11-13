"""Compatibility import; implementation lives in diagnostics.first_check_profile."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.diagnostics.first_check_profile")
sys.modules[__name__] = _implementation
