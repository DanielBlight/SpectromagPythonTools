"""Adapt mechanical gimbal angles to the existing map movement callback.

No raster generation or photocurrent acquisition is implemented here.
"""

from functools import partial

from spectromagpythontools.devices.pgm1se import PGM1SE


def gimbal_mover(
    mount: PGM1SE, *, cancel=None, tolerance_mrad=0.01, stable_s=0.2, timeout_s=10
):
    """Return move_and_wait(x_mrad, y_mrad), returning SDK readback angles.

    Use position_unit="mrad" in scan_map. This is not a sample-space calibration.
    The caller must connect and explicitly prepare the mount first.
    """
    return partial(
        mount.move_and_wait,
        cancel=cancel,
        tolerance_mrad=tolerance_mrad,
        stable_s=stable_s,
        timeout_s=timeout_s,
    )


# Descriptive aliases; preserve existing imports and object identity.
make_mirror_mover = gimbal_mover
