"""Repeat an explicitly defined map. No hardware access on import."""

import argparse
import os
from datetime import datetime
from pathlib import Path

from spectromagpythontools.configuration import (
    axis_values,
    build_mount,
    build_smu,
    load_config,
    measurement,
    mount_metadata,
)
from spectromagpythontools.routines.gimbal_positioning import gimbal_mover
from spectromagpythontools.routines.map_result import scan_map_result


def acquire(
    config_path, output_csv, *, xs, ys, samples=3, settle_s=0.1, serpentine=True
):
    """Return MapResult, always saving a new CSV. Parent directory must exist.

    Uses verified motion, independent of GUI preferences and leftover fast mode.
    Do not run concurrent instrument users in this process or another process.
    """
    config = load_config(config_path)
    max_v, max_i, settings = measurement(config["photocurrent_map"])
    motion = config["photocurrent_map"]
    previous = os.environ.get("SPECTROMAG_MIRROR_POLICY")
    os.environ["SPECTROMAG_MIRROR_POLICY"] = "verified"
    try:
        with build_mount(config["pgm1se"]) as mirror:
            mirror.prepare_closed_loop()
            with build_smu(
                config["keithley"], max_abs_bias_v=max_v, max_current_a=max_i
            ) as smu:
                smu.configure(settings)
                tolerance = motion.get("mirror_tolerance_mrad", 0.005)
                stable = motion.get("mirror_stable_s", 0.05)
                timeout = motion.get("mirror_timeout_s", 10.0)
                move = gimbal_mover(
                    mirror, tolerance_mrad=tolerance, stable_s=stable, timeout_s=timeout
                )
                return scan_map_result(
                    smu,
                    xs=xs,
                    ys=ys,
                    move_and_wait=move,
                    output_csv=output_csv,
                    samples=samples,
                    settle_s=settle_s,
                    serpentine=serpentine,
                    position_unit="mrad",
                    acquisition_metadata={
                        "config_path": str(Path(config_path).resolve()),
                        "mount": mount_metadata(mirror),
                        "motion": {
                            "policy": "verified",
                            "tolerance_mrad": tolerance,
                            "stable_s": stable,
                            "timeout_s": timeout,
                        },
                    },
                )
    finally:
        if previous is None:
            os.environ.pop("SPECTROMAG_MIRROR_POLICY", None)
        else:
            os.environ["SPECTROMAG_MIRROR_POLICY"] = previous


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config/instruments.local.toml")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--repeats", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 1000:
        parser.error("--repeats must be in 1..1000")
    # Edit these once to define the experiment. Coordinates are mirror mrad.
    xs = axis_values(-0.1, 0.1, 5)
    ys = axis_values(-0.1, 0.1, 5)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for run in range(args.repeats):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        path = args.output_dir / f"map-{stamp}-{run + 1:03d}.csv"
        result = acquire(
            args.config, path, xs=xs, ys=ys, samples=3, settle_s=0.1, serpentine=True
        )
        current_a = result.to_numpy()
        print(f"Saved {path}; array shape={current_a.shape}, units=A")
        # If pandas is installed: frame = result.to_dataframe()


if __name__ == "__main__":
    main()
