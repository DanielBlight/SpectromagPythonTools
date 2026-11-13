"""Compatibility import; implementation lives in application.operator_profile."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.application.operator_profile")
sys.modules[__name__] = _implementation
