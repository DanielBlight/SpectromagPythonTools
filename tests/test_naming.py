"""Descriptive names retain old object identities and behaviour."""

import importlib
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

PAIRS = [
    ("devices.keithley2600", "Keithley2600", "KeithleyPhotocurrent"),
    ("devices.keithley2600", "MeasurementSettings", "Settings"),
    ("devices.keithley2600", "MeasurementReading", "Reading"),
    ("routines.photocurrent_map", "PointMeasurement", "Point"),
    ("routines.photocurrent_map", "measure_point", "read_point"),
    ("routines.photocurrent_map", "acquire_current_map", "scan_map"),
    ("routines.gimbal_positioning", "make_mirror_mover", "gimbal_mover"),
    ("application.control", "InstrumentSession", "Session"),
    ("application.control", "InstrumentWorker", "HardwareWorker"),
    ("application.operator_control", "OperatorInstrumentSession", "OperatorSession"),
    ("gui.operator", "OperatorApplication", "Application"),
    ("gui.commissioning", "CommissioningApplication", "Application"),
    ("data.map_files", "load_map_for_display", "read_map"),
]


@pytest.mark.parametrize("module,new,old", PAIRS)
def test_alias_identity(module, new, old):
    module = importlib.import_module("spectromagpythontools." + module)
    assert getattr(module, new) is getattr(module, old)


def test_settings_fields_unchanged():
    from spectromagpythontools.devices.keithley2600 import MeasurementSettings

    assert asdict(MeasurementSettings(0, 1e-6)) == dict(
        bias_v=0,
        current_limit_a=1e-6,
        nplc=1.0,
        current_range_a=None,
        remote_sense=False,
    )


def test_canonical_class_names():
    from spectromagpythontools.devices.keithley2600 import (
        Keithley2600,
        MeasurementSettings,
        MeasurementReading,
    )

    assert [
        cls.__name__ for cls in (Keithley2600, MeasurementSettings, MeasurementReading)
    ] == ["Keithley2600", "MeasurementSettings", "MeasurementReading"]


def test_operator_help_no_hardware():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "spectromagpythontools.cli.operator_gui", "--help"],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src")),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
