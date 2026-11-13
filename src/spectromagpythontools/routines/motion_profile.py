"""Compatibility import; implementation lives in diagnostics.motion_profile."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.diagnostics.motion_profile")
sys.modules[__name__] = _implementation
