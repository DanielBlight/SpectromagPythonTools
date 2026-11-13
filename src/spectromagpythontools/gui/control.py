"""Compatibility import; implementation lives in application.control."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.application.control")
sys.modules[__name__] = _implementation
