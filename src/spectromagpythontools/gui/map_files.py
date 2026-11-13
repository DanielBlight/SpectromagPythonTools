"""Compatibility import; implementation lives in data.map_files."""

import sys
from importlib import import_module

_implementation = import_module("spectromagpythontools.data.map_files")
sys.modules[__name__] = _implementation
