"""Compatibility import; implementation lives in application.operator_control."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.application.operator_control")
sys.modules[__name__] = _implementation
