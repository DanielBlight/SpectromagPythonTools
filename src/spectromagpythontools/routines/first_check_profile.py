"""First-check diagnostics from existing readbacks only; no hardware access."""

import json
from pathlib import Path


def record_first_check(mount, initial, measured, x, y, tolerance, delay, error):
    record = getattr(mount, "_active_motion_profile", None)
    if record is None:
        return
    check = {
        "target_x_mrad": x,
        "target_y_mrad": y,
        "initial_x_mrad": initial.x_mrad,
        "initial_y_mrad": initial.y_mrad,
        "measured_x_mrad": measured.x_mrad,
        "measured_y_mrad": measured.y_mrad,
        "error_x_mrad": measured.x_mrad - x,
        "error_y_mrad": measured.y_mrad - y,
        "max_abs_error_mrad": error,
        "tolerance_mrad": tolerance,
        "configured_delay_s": delay,
        "within_tolerance": error <= tolerance,
        "unchanged_from_initial": (
            measured.x_mrad == initial.x_mrad and measured.y_mrad == initial.y_mrad
        ),
    }
    for label, position in (("initial", initial), ("measured", measured)):
        for axis in ("x", "y"):
            state = getattr(position, axis + "_state", None)
            if state is not None:
                check[f"{label}_{axis}_raw"] = state.raw_position
                check[f"{label}_{axis}_status_bits"] = state.status_bits
    record["first_check"] = check


def enrich_summary(result, csv_path):
    path = Path(csv_path).with_suffix(".motion.txt")
    if not path.exists():
        return
    checks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            check = json.loads(line).get("first_check")
            if check is not None:
                checks.append(check)
    if not checks:
        return
    errors = [check["max_abs_error_mrad"] for check in checks]
    result["first_check_summary"] = {
        "recorded_moves": len(checks),
        "within_tolerance": sum(check["within_tolerance"] for check in checks),
        "outside_tolerance": sum(not check["within_tolerance"] for check in checks),
        "unchanged_from_initial_outside_tolerance": sum(
            check["unchanged_from_initial"] and not check["within_tolerance"]
            for check in checks
        ),
        "max_abs_error_mrad": max(errors),
        "mean_abs_error_mrad": sum(errors) / len(errors),
        "tolerances_mrad": sorted({check["tolerance_mrad"] for check in checks}),
        "configured_delays_s": sorted(
            {check["configured_delay_s"] for check in checks}
        ),
        "interpretation": "Unchanged readback is an observation, not proof of stale SDK data. No extra reads were made.",
    }
