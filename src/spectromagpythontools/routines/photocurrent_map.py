"""Step-and-settle photocurrent mapping, independent of GUI code."""

from __future__ import annotations

import csv
import json
import statistics
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from spectromagpythontools.devices.keithley2600 import (
    AcquisitionCancelled,
    InstrumentError,
    KeithleyPhotocurrent,
    Reading,
    finite,
)


@dataclass(frozen=True)
class Point:
    readings: tuple[Reading, ...]
    mean_current_a: float
    sample_std_current_a: float | None
    mean_voltage_v: float
    settle_elapsed_s: float = 0.0
    acquisition_elapsed_s: float = 0.0


def read_point(
    smu: KeithleyPhotocurrent,
    *,
    samples: int = 1,
    settle_s: float = 0,
    cancel: threading.Event | None = None,
) -> Point:
    """Settle and average readings at one stationary position.

    The caller owns the acquisition session and must not share it concurrently.
    On acquisition failure, attempt output disable without hiding the exception.
    """
    if type(samples) is not int or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer in 1..1000")
    settle_s = finite(settle_s, "settle_s")
    if not 0 <= settle_s <= 60:
        raise ValueError("settle_s must be in 0..60 seconds")

    try:
        settle_started = time.perf_counter()
        if cancel is None:
            time.sleep(settle_s)
        elif cancel.wait(settle_s):
            raise AcquisitionCancelled("Cancelled during settling")

        settle_elapsed = time.perf_counter() - settle_started
        acquisition_started = time.perf_counter()
        batch_reader = getattr(smu, "read_many", None)
        if callable(batch_reader) and samples > 1:
            readings = list(batch_reader(samples, cancel=cancel))
        else:
            readings = []
            for _ in range(samples):
                if cancel is not None and cancel.is_set():
                    raise AcquisitionCancelled("Cancelled before measurement")
                readings.append(smu.read())
        acquisition_elapsed = time.perf_counter() - acquisition_started
        if len(readings) != samples:
            raise InstrumentError("Measurement returned the wrong number of readings")
        if cancel is not None and cancel.is_set():
            raise AcquisitionCancelled("Cancelled after measurement")

        currents = [reading.current_a for reading in readings]
        return Point(
            tuple(readings),
            statistics.mean(currents),
            statistics.stdev(currents) if samples > 1 else None,
            statistics.mean(reading.voltage_v for reading in readings),
            settle_elapsed,
            acquisition_elapsed,
        )
    except BaseException as exc:
        try:
            smu.disable_output()
        except BaseException as cleanup:  # noqa: BLE001
            exc.add_note(f"Output disable could not be confirmed: {cleanup}")
        raise


def scan_map(
    smu: KeithleyPhotocurrent,
    *,
    xs,
    ys,
    move_and_wait: Callable[[float, float], tuple[float, float]],
    output_csv,
    samples=1,
    settle_s=0.1,
    serpentine=True,
    position_unit="um",
    cancel: threading.Event | None = None,
    on_pixel=None,
    acquisition_metadata=None,
):
    """Stream pixels to a NEW CSV; retain partial data on failure.

    move_and_wait must BLOCK until BOTH axes have arrived and return measured
    coordinates in position_unit. No laser/shutter control is performed here.
    A completion comment distinguishes a finished map from a partial map.
    """
    xs, ys = (tuple(finite(x, "x") for x in xs), tuple(finite(y, "y") for y in ys))
    if not xs or not ys or len(xs) * len(ys) > 1000000:
        raise ValueError("Require 1..1,000,000 pixels")
    if len(set(xs)) != len(xs) or len(set(ys)) != len(ys):
        raise ValueError("Duplicate axis coordinates are not allowed")
    if type(samples) is not int or not 1 <= samples <= 1000:
        raise ValueError("samples must be an integer in 1..1000")
    settle_s = finite(settle_s, "settle_s")
    if not 0 <= settle_s <= 60 or type(serpentine) is not bool:
        raise ValueError("Invalid settling time or serpentine flag")
    if (
        not callable(move_and_wait)
        or not isinstance(position_unit, str)
        or (not position_unit.strip())
    ):
        raise ValueError("Provide blocking movement callback and coordinate unit")
    if smu.settings is None:
        raise InstrumentError("Connect and configure before scanning")
    if on_pixel is not None and not callable(on_pixel):
        raise TypeError("on_pixel must be callable or None")
    if acquisition_metadata is not None and not isinstance(acquisition_metadata, dict):
        raise TypeError("acquisition_metadata must be a dictionary or None")
    metadata = {
        "schema": "photocurrent-map-v2",
        "created_host_unix_s": time.time(),
        "identity": smu.identity,
        "resource": smu.resource,
        "channel": smu.channel,
        "settings": asdict(smu.settings),
        "line_frequency_hz": smu.line_frequency_hz,
        "position_unit": position_unit,
        "xs": xs,
        "ys": ys,
        "samples": samples,
        "settle_s": settle_s,
        "serpentine": serpentine,
        "max_abs_bias_v": smu.max_abs_bias_v,
        "max_current_a": smu.max_current_a,
        "timestamp_basis": "host Unix seconds; batched readings share batch bounds, not per-conversion times",
        "measurement_transport": "TSP batches of up to 3 for multiple samples; individual raw readings retained",
        "timing_basis": "host perf_counter elapsed seconds; acquisition includes communication and checks",
    }
    import os

    metadata["mirror_policy"] = {
        "name": os.environ.get("SPECTROMAG_MIRROR_POLICY", "verified"),
        "extra_delay_s": os.environ.get("SPECTROMAG_MIRROR_DELAY_S", "0.05"),
        "full_check_interval_s": os.environ.get("SPECTROMAG_MIRROR_FULL_CHECK_S", "2"),
        "freshness": "cached SDK readback; not response-acknowledged",
        "one_check_semantics": "one in-tolerance observation, not continuous stability; verified fallback",
    }
    if acquisition_metadata is not None:
        metadata["acquisition"] = acquisition_metadata
    # Validate JSON before creating the output file or enabling output.
    metadata_json = json.dumps(metadata, allow_nan=False)
    if cancel is not None and cancel.is_set():
        raise AcquisitionCancelled("Cancelled before output enable")
    count = 0
    totals = {
        "motion_s": 0.0,
        "settle_s": 0.0,
        "acquisition_s": 0.0,
        "save_s": 0.0,
        "callback_s": 0.0,
    }
    scan_started = time.perf_counter()
    with Path(output_csv).open("x", newline="", encoding="utf-8") as stream:
        stream.write("# " + metadata_json + "\n")
        writer = csv.writer(stream)
        writer.writerow(
            [
                "row",
                "column",
                "requested_x",
                "requested_y",
                "actual_x",
                "actual_y",
                "mean_current_a",
                "sample_std_current_a",
                "mean_voltage_v",
                "readings_json",
                "motion_s",
                "settle_elapsed_s",
                "acquisition_s",
            ]
        )
        stream.flush()
        try:
            with smu.output_enabled():
                for row, y in enumerate(ys):
                    columns = (
                        range(len(xs) - 1, -1, -1)
                        if serpentine and row % 2
                        else range(len(xs))
                    )
                    for column in columns:
                        if cancel is not None and cancel.is_set():
                            raise AcquisitionCancelled("Cancelled before motion")
                        motion_started = time.perf_counter()
                        actual_x, actual_y = move_and_wait(xs[column], y)
                        motion_elapsed = time.perf_counter() - motion_started
                        actual_x, actual_y = (
                            finite(actual_x, "actual_x"),
                            finite(actual_y, "actual_y"),
                        )
                        point = read_point(
                            smu, samples=samples, settle_s=settle_s, cancel=cancel
                        )
                        totals["motion_s"] += motion_elapsed
                        totals["settle_s"] += point.settle_elapsed_s
                        totals["acquisition_s"] += point.acquisition_elapsed_s
                        save_started = time.perf_counter()
                        writer.writerow(
                            [
                                row,
                                column,
                                xs[column],
                                y,
                                actual_x,
                                actual_y,
                                point.mean_current_a,
                                point.sample_std_current_a,
                                point.mean_voltage_v,
                                json.dumps(
                                    [asdict(r) for r in point.readings], allow_nan=False
                                ),
                                motion_elapsed,
                                point.settle_elapsed_s,
                                point.acquisition_elapsed_s,
                            ]
                        )
                        stream.flush()
                        totals["save_s"] += time.perf_counter() - save_started
                        count += 1
                        if on_pixel is not None:
                            callback_started = time.perf_counter()
                            on_pixel(
                                {
                                    "row": row,
                                    "column": column,
                                    "requested_x": xs[column],
                                    "requested_y": y,
                                    "actual_x": actual_x,
                                    "actual_y": actual_y,
                                    "current_a": point.mean_current_a,
                                    "voltage_v": point.mean_voltage_v,
                                    "completed": count,
                                    "total": len(xs) * len(ys),
                                    "timing": {
                                        "motion_s": motion_elapsed,
                                        "settle_s": point.settle_elapsed_s,
                                        "acquisition_s": point.acquisition_elapsed_s,
                                    },
                                }
                            )
                            totals["callback_s"] += (
                                time.perf_counter() - callback_started
                            )
        except BaseException as exc:
            try:
                stream.write(
                    "# "
                    + json.dumps(
                        {
                            "status": "partial",
                            "pixels": count,
                            "error": str(exc),
                            "timing_totals_s": totals,
                            "elapsed_s": time.perf_counter() - scan_started,
                        }
                    )
                    + "\n"
                )
            except BaseException as cleanup:  # noqa: BLE001
                exc.add_note(f"Could not write partial footer: {cleanup}")
            raise
        stream.write(
            "# "
            + json.dumps(
                {
                    "status": "complete",
                    "pixels": count,
                    "timing_totals_s": totals,
                    "elapsed_s": time.perf_counter() - scan_started,
                }
            )
            + "\n"
        )
    return count
