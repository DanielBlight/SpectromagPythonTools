"""Compatibility import; implementation lives in devices.scan_position_cache."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.devices.scan_position_cache")
sys.modules[__name__] = _implementation
