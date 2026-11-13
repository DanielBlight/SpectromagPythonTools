"""Benchmark measurement and mirror settings without running a map.

This script:
- compares 3 readings at 1 NPLC with 1 reading at 3 NPLC;
- optionally compares PPC102 SDK response waits;
- retains verified mirror move_and_wait checks;
- writes a timestamped JSON report;
- does not edit configuration or generate synthetic measurements.

The operator must provide sample-approved electrical limits.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from spectromagpythontools.configuration import (
    build_mount,
    build_smu,
    load_config,
)
from spectromagpythontools.devices.keithley2600 import Settings
from spectromagpythontools.routines.photocurrent_map import read_point


def finite(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not bool")

    result = float(value)

    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")

    return result


def summary(values):
    return {
        "count": len(values),
        "mean": statistics.mean(values),
        "sample_std": (statistics.stdev(values) if len(values) > 1 else None),
        "minimum": min(values),
        "maximum": max(values),
    }


def benchmark_keithley(args, config):
    print()
    print("KEITHLEY BENCHMARK")
    print("==================")
    print("This will apply the requested bias during each test block.")
    print("Output-off is attempted and verified by the existing wrapper.")
    print()

    bias = finite(args.bias, "bias")
    compliance = finite(args.compliance, "compliance")
    max_bias = finite(args.dut_max_bias, "DUT maximum bias")
    max_current = finite(
        args.dut_max_current,
        "DUT maximum current",
    )

    if not 0 <= max_bias <= 20:
        raise ValueError("DUT maximum bias must be in 0..20 V")

    if not 0 < max_current <= 0.1:
        raise ValueError("DUT maximum current must be in 0..0.1 A")

    if abs(bias) > max_bias:
        raise ValueError("Requested bias exceeds DUT maximum bias")

    if not 0 < compliance <= max_current:
        raise ValueError(
            "Compliance must be positive and no greater than the DUT maximum current"
        )

    current_range = args.current_range

    if current_range is not None:
        current_range = finite(
            current_range,
            "current range",
        )

        if current_range <= 0:
            raise ValueError("Current range must be positive")

    modes = (
        {
            "name": "three_readings_at_1_nplc",
            "nplc": 1.0,
            "samples": 3,
        },
        {
            "name": "one_reading_at_3_nplc",
            "nplc": 3.0,
            "samples": 1,
        },
    )

    results = []

    for mode in modes:
        print()
        print(f"Running {mode['name']} for {args.repeats} repeated points...")

        settings = Settings(
            bias_v=bias,
            current_limit_a=compliance,
            nplc=mode["nplc"],
            current_range_a=current_range,
            remote_sense=args.remote_sense,
        )

        point_currents = []
        acquisition_times = []
        point_records = []

        smu = build_smu(
            config["keithley"],
            max_abs_bias_v=max_bias,
            max_current_a=max_current,
        )

        with smu:
            smu.configure(settings)

            with smu.output_enabled():
                # Discard one warm-up result to reduce mode-change
                # and output-enable transients in the comparison.
                read_point(
                    smu,
                    samples=mode["samples"],
                    settle_s=args.settle,
                )

                for index in range(args.repeats):
                    point = read_point(
                        smu,
                        samples=mode["samples"],
                        settle_s=args.settle,
                    )

                    point_currents.append(point.mean_current_a)
                    acquisition_times.append(point.acquisition_elapsed_s)

                    point_records.append(
                        {
                            "repeat": index + 1,
                            "point": asdict(point),
                        }
                    )

        result = {
            **mode,
            "repeats": args.repeats,
            "current_a": summary(point_currents),
            "acquisition_s": summary(acquisition_times),
            "points": point_records,
        }

        results.append(result)

        print(f"  Mean acquisition time: {result['acquisition_s']['mean']:.4f} s")
        print(f"  Mean measured current: {result['current_a']['mean']:.6g} A")
        print(
            "  Between-point standard deviation: "
            f"{result['current_a']['sample_std']:.6g} A"
        )

    return {
        "electrical_settings": {
            "bias_v": bias,
            "current_limit_a": compliance,
            "dut_max_abs_bias_v": max_bias,
            "dut_max_current_a": max_current,
            "current_range_a": current_range,
            "remote_sense": args.remote_sense,
            "settle_s": args.settle,
        },
        "modes": results,
        "interpretation": {
            "three_at_one_nplc": (
                "Retains three raw readings and a per-point sample standard deviation."
            ),
            "one_at_three_nplc": (
                "Uses the same nominal total integration aperture "
                "but loses within-point sample standard deviation "
                "and transient information."
            ),
        },
    }


def benchmark_mirror(args, config):
    print()
    print("MIRROR BENCHMARK")
    print("================")
    print("The laser must be blocked.")
    print("Each target is still checked using move_and_wait.")
    print()

    step = finite(args.mirror_step, "mirror step")
    tolerance = finite(
        args.mirror_tolerance,
        "mirror tolerance",
    )
    stable = finite(
        args.stable_interval,
        "stable interval",
    )
    timeout = finite(
        args.move_timeout,
        "move timeout",
    )

    if not 0 < abs(step) <= 0.05:
        raise ValueError("Mirror benchmark step must be >0 and <=0.05 mrad")

    waits = [
        finite(value.strip(), "response wait")
        for value in args.response_waits.split(",")
    ]

    results = []

    for response_wait in waits:
        if not 0.02 <= response_wait <= 0.5:
            raise ValueError("Each response wait must be in 0.02..0.5 s")

        local_config = deepcopy(config["pgm1se"])
        local_config["response_wait_s"] = response_wait

        print()
        print(f"Testing response_wait_s={response_wait:g} s...")

        move_times = []
        position_errors = []
        records = []

        with build_mount(local_config) as mount:
            mount.prepare_closed_loop()
            start = mount.position()

            start_x = start.x_mrad
            start_y = start.y_mrad

            if abs(start_x + step) > mount.max_abs_mrad:
                step = -abs(step)

            targets = []

            for _ in range(args.mirror_cycles):
                targets.append((start_x + step, start_y))
                targets.append((start_x, start_y))

            for index, (target_x, target_y) in enumerate(
                targets,
                start=1,
            ):
                started = time.perf_counter()

                actual_x, actual_y = mount.move_and_wait(
                    target_x,
                    target_y,
                    tolerance_mrad=tolerance,
                    stable_s=stable,
                    timeout_s=timeout,
                )

                elapsed = time.perf_counter() - started
                error = max(
                    abs(actual_x - target_x),
                    abs(actual_y - target_y),
                )

                move_times.append(elapsed)
                position_errors.append(error)

                records.append(
                    {
                        "move": index,
                        "target_x_mrad": target_x,
                        "target_y_mrad": target_y,
                        "actual_x_mrad": actual_x,
                        "actual_y_mrad": actual_y,
                        "error_mrad": error,
                        "elapsed_s": elapsed,
                    }
                )

        result = {
            "response_wait_s": response_wait,
            "moves": len(records),
            "move_time_s": summary(move_times),
            "position_error_mrad": summary(position_errors),
            "records": records,
        }

        results.append(result)

        print(f"  Mean verified move time: {result['move_time_s']['mean']:.4f} s")
        print(
            "  Maximum position error: "
            f"{result['position_error_mrad']['maximum']:.6g} mrad"
        )

    return {
        "mirror_step_mrad": step,
        "cycles": args.mirror_cycles,
        "tolerance_mrad": tolerance,
        "stable_interval_s": stable,
        "timeout_s": timeout,
        "modes": results,
        "warning": (
            "The SDK readback remains cached and is not a "
            "hardware-timestamped freshness guarantee."
        ),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "--config",
        type=Path,
        default=Path(
            r"C:\SpectromagPythonTools"
            r"\config\instruments.local.toml"
        ),
    )

    parser.add_argument(
        "--output-directory",
        type=Path,
        default=Path(r"C:\SpectromagPythonTools\logs"),
    )

    parser.add_argument(
        "--keithley",
        action="store_true",
        help="Run the Keithley averaging benchmark.",
    )

    parser.add_argument(
        "--mirror",
        action="store_true",
        help="Run the blocked-laser mirror benchmark.",
    )

    parser.add_argument(
        "--bias",
        type=float,
    )

    parser.add_argument(
        "--compliance",
        type=float,
    )

    parser.add_argument(
        "--dut-max-bias",
        type=float,
    )

    parser.add_argument(
        "--dut-max-current",
        type=float,
    )

    parser.add_argument(
        "--current-range",
        type=float,
        default=None,
    )

    parser.add_argument(
        "--remote-sense",
        action="store_true",
    )

    parser.add_argument(
        "--settle",
        type=float,
        default=0.05,
    )

    parser.add_argument(
        "--repeats",
        type=int,
        default=10,
    )

    parser.add_argument(
        "--mirror-step",
        type=float,
        default=0.005,
    )

    parser.add_argument(
        "--mirror-cycles",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--mirror-tolerance",
        type=float,
        default=0.001,
    )

    parser.add_argument(
        "--stable-interval",
        type=float,
        default=0.05,
    )

    parser.add_argument(
        "--move-timeout",
        type=float,
        default=10.0,
    )

    parser.add_argument(
        "--response-waits",
        default="0.10,0.05",
        help=("Comma-separated PPC102 SDK response waits. Default: 0.10,0.05"),
    )

    args = parser.parse_args()

    if not args.keithley and not args.mirror:
        parser.error("Select --keithley, --mirror, or both.")

    if args.repeats < 3:
        parser.error("--repeats must be at least 3.")

    if args.mirror_cycles < 1:
        parser.error("--mirror-cycles must be at least 1.")

    if args.keithley:
        required = {
            "--bias": args.bias,
            "--compliance": args.compliance,
            "--dut-max-bias": args.dut_max_bias,
            "--dut-max-current": args.dut_max_current,
        }

        missing = [name for name, value in required.items() if value is None]

        if missing:
            parser.error("Keithley benchmark requires: " + ", ".join(missing))

    config = load_config(args.config)

    report = {
        "schema": "spectromag-speed-benchmark-v1",
        "created": datetime.now().astimezone().isoformat(),
        "config_path": str(args.config.resolve()),
        "hardware_data": True,
    }

    if args.keithley:
        report["keithley"] = benchmark_keithley(
            args,
            config,
        )

    if args.mirror:
        report["mirror"] = benchmark_mirror(
            args,
            config,
        )

    output_directory = args.output_directory.resolve()
    output_directory.mkdir(
        parents=True,
        exist_ok=True,
    )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")

    output = output_directory / (f"speed-benchmark-{stamp}.json")

    output.write_text(
        json.dumps(
            report,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )

    print()
    print("Benchmark complete.")
    print("Report:", output)
    print("No configuration file was modified.")


if __name__ == "__main__":
    main()
