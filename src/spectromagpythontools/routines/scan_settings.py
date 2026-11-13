"""Compatibility import; implementation lives in devices.scan_settings."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.devices.scan_settings")
sys.modules[__name__] = _implementation
