"""Acquire a map and return an offline result; legacy result imports remain valid."""

from spectromagpythontools.data.map_result import MapResult, load_map_result
from spectromagpythontools.routines.photocurrent_map import scan_map

__all__ = ["MapResult", "load_map_result", "scan_map_result"]


def scan_map_result(smu, *, xs, ys, move_and_wait, output_csv, **kwargs):
    """Run the existing engine and return MapResult; errors preserve partial CSV.

    Caller connects/configures instruments and prepares closed-loop motion.
    Existing scan_map callers continue to receive an integer pixel count.
    """
    scan_map(
        smu, xs=xs, ys=ys, move_and_wait=move_and_wait, output_csv=output_csv, **kwargs
    )
    return load_map_result(output_csv)
