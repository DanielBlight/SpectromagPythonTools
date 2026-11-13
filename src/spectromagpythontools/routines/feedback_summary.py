"""Compatibility import; implementation lives in diagnostics.feedback_summary."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.diagnostics.feedback_summary")
sys.modules[__name__] = _implementation
