"""Small TOML configuration helpers. No hardware access or GUI imports."""

from __future__ import annotations

import math
import re
import tomllib
from dataclasses import asdict
from pathlib import Path

from spectromagpythontools.devices.keithley2600 import Keithley2600, MeasurementSettings
from spectromagpythontools.devices.pgm1se import PGM1SE, AxisCalibration, KinesisPPC102


def load_config(path):
    with Path(path).open("rb") as stream:
        config = tomllib.load(stream)
    for section in ("pgm1se", "keithley", "photocurrent_map"):
        if section in config and not isinstance(config[section], dict):
            raise ValueError(f"[{section}] must be a TOML table")
    return config


def numeric(value, name, low, high):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be a number, not bool")
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be in {low}..{high}")
    return result


def integer(value, name, low, high):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer")
    number = numeric(value, name, low, high)
    if not number.is_integer():
        raise ValueError(f"{name} must be an integer")
    return int(number)


def calibration(section, axis):
    values = section.get(f"{axis}_calibration")
    if values is None:
        return None
    if not isinstance(values, dict) or values.get("verified") is not True:
        raise ValueError(f"{axis.upper()} calibration requires verified = true")
    return AxisCalibration(
        values["raw_minus_10"], values["raw_plus_10"], values["evidence"]
    )


def build_mount(section, *, backend=None):
    serial = section.get("serial", "")
    if not isinstance(serial, str) or not re.fullmatch(r"95[0-9]{6}", serial):
        raise ValueError("Replace the PPC102 serial placeholder before connecting")
    native = (
        backend
        if backend is not None
        else KinesisPPC102(
            section.get("kinesis_directory", r"C:\Program Files\Thorlabs\Kinesis"),
            section.get("response_wait_s", 0.1),
        )
    )
    return PGM1SE(
        serial,
        x_channel=section.get("x_channel", 1),
        y_channel=section.get("y_channel", 2),
        x_calibration=calibration(section, "x"),
        y_calibration=calibration(section, "y"),
        max_abs_mrad=section.get("max_abs_mrad", 10),
        backend=native,
    )


def build_smu(section, *, max_abs_bias_v, max_current_a):
    resource = section.get("resource", "")
    if (
        not isinstance(resource, str)
        or not resource.upper().startswith("USB")
        or not resource.upper().endswith("::INSTR")
        or any(word in resource.upper() for word in ("PLACEHOLDER", "XXXX"))
    ):
        raise ValueError("Replace the Keithley USB VISA resource placeholder")
    return Keithley2600(
        resource,
        expected_model=section.get("model", ""),
        channel=section.get("channel", "a"),
        timeout_ms=section.get("timeout_ms", 5000),
        visa_library=section.get("visa_library", ""),
        max_abs_bias_v=max_abs_bias_v,
        max_current_a=max_current_a,
    )


def axis_values(start, stop, count):
    start = numeric(start, "axis start (mrad)", -10, 10)
    stop = numeric(stop, "axis end (mrad)", -10, 10)
    count = integer(count, "axis points", 1, 100)
    if count == 1:
        if start != stop:
            raise ValueError("For one point, start and end must match")
        return (start,)
    if start == stop:
        raise ValueError("Multiple points require distinct start and end")
    return tuple(start + (stop - start) * i / (count - 1) for i in range(count))


def measurement(values):
    max_v = numeric(values["max_abs_bias_v"], "DUT voltage limit", 0, 20)
    max_i = numeric(values["max_current_a"], "DUT current limit", 1e-10, 0.1)
    bias = numeric(values["bias_v"], "bias (V)", -max_v, max_v)
    limit = numeric(values["current_limit_a"], "compliance (A)", 1e-10, max_i)
    nplc = numeric(values["nplc"], "NPLC", 0.001, 25)
    current_range = values.get("current_range_a", "")
    if current_range == "" or current_range is None:
        current_range = None
    else:
        current_range = numeric(current_range, "current range (A)", 1e-12, 1.5)
    remote = values.get("remote_sense", False)
    if type(remote) is not bool:
        raise TypeError("remote_sense must be bool")
    return max_v, max_i, MeasurementSettings(bias, limit, nplc, current_range, remote)


def mount_metadata(mount):
    return {
        "controller": "PPC102",
        "serial": mount.serial,
        "x_channel": mount.x_channel,
        "y_channel": mount.y_channel,
        "x_calibration": asdict(mount.x_calibration),
        "y_calibration": asdict(mount.y_calibration),
        "max_abs_mrad": mount.max_abs_mrad,
        "readback_basis": "SDK cache, host observation times; freshness not certified",
    }
