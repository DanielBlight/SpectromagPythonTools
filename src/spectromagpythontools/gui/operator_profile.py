"""Operator profile assembly. No hardware access; profile owns engineering settings."""

import os
from contextlib import contextmanager
from copy import deepcopy

from spectromagpythontools.configuration import calibration, numeric

MOTION_DEFAULTS = {
    "mirror_tolerance_mrad": 0.005,
    "mirror_stable_s": 0.05,
    "mirror_timeout_s": 10.0,
}
ENVIRONMENT = {
    "policy": ("SPECTROMAG_MIRROR_POLICY", "one_check"),
    "delay_s": ("SPECTROMAG_MIRROR_DELAY_S", 0.08),
    "feedback_request": ("SPECTROMAG_FEEDBACK_REQUEST", "combined"),
    "full_check_s": ("SPECTROMAG_MIRROR_FULL_CHECK_S", 5.0),
}
PROFILE_FIELDS = frozenset((*MOTION_DEFAULTS, "response_wait_s"))


def validate_profile(profile):
    for section in ("pgm1se", "keithley"):
        if not isinstance(profile.get(section), dict):
            raise TypeError(f"Profile requires [{section}]")
    for axis in ("x", "y"):
        if calibration(profile["pgm1se"], axis) is None:
            raise ValueError(f"Profile requires verified {axis.upper()} calibration")
    motion = {
        **MOTION_DEFAULTS,
        **{
            k: v
            for k, v in profile.get("photocurrent_map", {}).items()
            if k in MOTION_DEFAULTS
        },
    }
    numeric(motion["mirror_tolerance_mrad"], "profile tolerance", 1e-6, 1)
    stable = numeric(motion["mirror_stable_s"], "profile stable interval", 0.05, 10)
    timeout = numeric(motion["mirror_timeout_s"], "profile timeout", 0.1, 120)
    if timeout <= stable:
        raise ValueError("Profile timeout must exceed stable interval")
    timing = profile.get("operator_motion", {})
    if not isinstance(timing, dict):
        raise TypeError("operator_motion must be a table")
    if timing.get("policy", "one_check") not in ("one_check", "verified"):
        raise ValueError("Invalid profile mirror policy")
    if timing.get("feedback_request", "combined") not in (
        "legacy",
        "combined",
        "combined_refresh",
    ):
        raise ValueError("Invalid profile feedback request mode")
    numeric(timing.get("delay_s", 0.08), "profile movement delay", 0.05, 0.5)
    numeric(timing.get("full_check_s", 5), "profile refresh interval", 0.5, 5)
    return motion


def operation_config(profile, values, *, remote, serpentine):
    motion = validate_profile(profile)
    result = deepcopy(values)
    result.update(motion)
    result["pgm1se"] = deepcopy(profile["pgm1se"])
    result["keithley"] = deepcopy(profile["keithley"])
    result["operator_motion"] = deepcopy(profile.get("operator_motion", {}))
    result["bias_v"] = numeric(result["bias_v"], "bias (V)", -20, 20)
    result["max_abs_bias_v"] = abs(result["bias_v"])
    result["current_limit_a"] = result["max_current_a"]
    result["remote_sense"] = remote
    result["serpentine"] = serpentine
    result["application"] = "SpectromagPythonTools operator GUI"
    return result


@contextmanager
def motion_environment(config):
    """Existing driver consumes environment; scope profile values to worker operation.

    The single GUI worker owns hardware. No concurrent instrument clients allowed.
    Restore prior environment even on cancellation or error.
    """
    timing = config.get("operator_motion", {})
    old = {name: os.environ.get(name) for name, _ in ENVIRONMENT.values()}
    try:
        for key, (name, default) in ENVIRONMENT.items():
            os.environ[name] = str(timing.get(key, default))
        yield
    finally:
        for name, value in old.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
