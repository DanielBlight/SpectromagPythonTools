"""Integration example only. Importing/running this file does not access hardware."""
from spectromagpythontools.devices.keithley2600 import (
    KeithleyPhotocurrent,
    Settings,
)
from spectromagpythontools.routines.photocurrent_map import scan_map


def acquire_map(*, resource, exact_model, max_abs_bias_v, max_current_a,
                bias_v, current_limit_a, xs, ys, move_and_wait, output_csv,
                channel="a", position_unit="um", cancel=None):
    # All DUT limits and instrument identification are supplied by the caller.
    # move_and_wait(x, y) must block until motion completes, enforce travel
    # limits, and return actual readback coordinates in position_unit.
    with KeithleyPhotocurrent(
        resource, expected_model=exact_model, channel=channel,
        max_abs_bias_v=max_abs_bias_v, max_current_a=max_current_a,
    ) as smu:
        smu.configure(Settings(
            bias_v=bias_v, current_limit_a=current_limit_a,
            nplc=1.0, current_range_a=None,
        ))
        return scan_map(
            smu, xs=xs, ys=ys, move_and_wait=move_and_wait,
            output_csv=output_csv, samples=3, settle_s=0.1,
            position_unit=position_unit, cancel=cancel,
        )

