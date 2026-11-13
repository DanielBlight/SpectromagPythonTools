"""Fit PGM1SE/M raw PPC102 counts to manually recorded Kinesis angles.

No hardware connection, motion, or configuration edit is performed.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from dataclasses import asdict, dataclass
from datetime import datetime
from itertools import pairwise
from pathlib import Path

ANGLES = (-1.0, -0.5, 0.0, 0.5, 1.0)
COUNT_MIN, COUNT_MAX = -32767, 32767


@dataclass(frozen=True)
class Point:
    angle_mrad: float
    raw_count: int


@dataclass(frozen=True)
class Fit:
    axis: str
    slope_counts_per_mrad: float
    zero_count: float
    raw_minus_10: int
    raw_plus_10: int
    max_abs_residual_mrad: float
    rms_residual_mrad: float
    direction: str
    points: tuple[Point, ...]
    residuals_mrad: tuple[float, ...]


def finite(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not bool")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def count(value):
    number = finite(value, "raw count")
    if not number.is_integer() or not COUNT_MIN <= number <= COUNT_MAX:
        raise ValueError(f"raw count must be an integer in {COUNT_MIN}..{COUNT_MAX}")
    return int(number)


def fit_axis(axis, points, limit):
    if len(points) < 5:
        raise ValueError(f"{axis}: at least five pairs are required")
    angles = [p.angle_mrad for p in points]
    counts = [p.raw_count for p in points]
    if len(set(angles)) != len(angles):
        raise ValueError(f"{axis}: duplicate angles are not allowed")
    if min(angles) >= 0 or max(angles) <= 0 or max(angles) - min(angles) < 1:
        raise ValueError(
            f"{axis}: include negative and positive angles spanning at least 1 mrad"
        )
    mean_a, mean_c = statistics.mean(angles), statistics.mean(counts)
    denominator = sum((angle - mean_a) ** 2 for angle in angles)
    slope = (
        sum((angle - mean_a) * (raw - mean_c) for angle, raw in zip(angles, counts))
        / denominator
    )
    if abs(slope) < 1:
        raise ValueError(f"{axis}: implausibly small fitted scale")
    zero = mean_c - slope * mean_a
    predicted = [(raw - zero) / slope for raw in counts]
    residuals = tuple(actual - estimate for actual, estimate in zip(angles, predicted))
    maximum = max(abs(value) for value in residuals)
    rms = math.sqrt(statistics.mean(value * value for value in residuals))
    ordered = sorted(points, key=lambda point: point.angle_mrad)
    differences = [
        second.raw_count - first.raw_count for first, second in pairwise(ordered)
    ]
    if (slope > 0 and any(value <= 0 for value in differences)) or (
        slope < 0 and any(value >= 0 for value in differences)
    ):
        raise ValueError(f"{axis}: raw counts are not strictly monotonic")
    minus, plus = round(zero - 10 * slope), round(zero + 10 * slope)
    if not (COUNT_MIN <= minus <= COUNT_MAX and COUNT_MIN <= plus <= COUNT_MAX):
        raise ValueError(
            f"{axis}: extrapolated endpoints {minus}, {plus} exceed signed-count limits"
        )
    if maximum > limit:
        raise ValueError(
            f"{axis}: maximum residual {maximum:.6g} mrad exceeds {limit:.6g} mrad"
        )
    return Fit(
        axis,
        slope,
        zero,
        minus,
        plus,
        maximum,
        rms,
        "increasing" if slope > 0 else "decreasing",
        tuple(points),
        residuals,
    )


def prompt_axis(axis):
    print(
        f"\n{axis} axis: enter ACTUAL settled Kinesis angles and Python raw_position values."
    )
    points = []
    for suggested in ANGLES:
        while True:
            try:
                angle = finite(
                    input(f"Actual angle near {suggested:+.3f} mrad: ").strip(), "angle"
                )
                if not -2 <= angle <= 2:
                    raise ValueError(
                        "commissioning points must remain within +/-2 mrad"
                    )
                raw = count(input("Python raw_position: ").strip())
                points.append(Point(angle, raw))
                break
            except (TypeError, ValueError) as error:
                print(f"Invalid pair: {error}. Re-enter this pair.")
    return points


def load_csv(path):
    result = {"X": [], "Y": []}
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if not {"axis", "angle_mrad", "raw_count"}.issubset(reader.fieldnames or ()):
            raise ValueError("CSV columns must be axis,angle_mrad,raw_count")
        for row in reader:
            axis = row["axis"].strip().upper()
            if axis not in result:
                raise ValueError("axis must be X or Y")
            angle = finite(row["angle_mrad"], "angle")
            if not -2 <= angle <= 2:
                raise ValueError("commissioning points must remain within +/-2 mrad")
            result[axis].append(Point(angle, count(row["raw_count"])))
    return result


def fit_data(fit):
    data = asdict(fit)
    data["points"] = [asdict(point) for point in fit.points]
    return data


def snippet(x, y, evidence):
    safe = evidence.replace("\\", "\\\\").replace('"', '\\"')
    return (
        "# CANDIDATE ONLY: review before copying into instruments.local.toml.\n"
        "[pgm1se.x_calibration]\n"
        "verified = true\n"
        f"raw_minus_10 = {x.raw_minus_10}\n"
        f"raw_plus_10 = {x.raw_plus_10}\n"
        f'evidence = "{safe}; X max residual {x.max_abs_residual_mrad:.6g} mrad"\n\n'
        "[pgm1se.y_calibration]\n"
        "verified = true\n"
        f"raw_minus_10 = {y.raw_minus_10}\n"
        f"raw_plus_10 = {y.raw_plus_10}\n"
        f'evidence = "{safe}; Y max residual {y.max_abs_residual_mrad:.6g} mrad"\n'
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--csv", type=Path, help="CSV with axis,angle_mrad,raw_count columns"
    )
    parser.add_argument("--output-directory", type=Path, default=Path("logs"))
    parser.add_argument("--max-residual-mrad", type=float, default=0.002)
    parser.add_argument(
        "--evidence",
        default=(
            "PGM1SE/M matched pair; Kinesis actual angles compared with PPC2_GetPosition raw readback"
        ),
    )
    args = parser.parse_args()
    limit = finite(args.max_residual_mrad, "residual limit")
    if not 0 < limit <= 0.01:
        raise SystemExit("Residual limit must be >0 and <=0.01 mrad")
    observations = (
        load_csv(args.csv)
        if args.csv
        else {"X": prompt_axis("X"), "Y": prompt_axis("Y")}
    )
    try:
        x = fit_axis("X", observations["X"], limit)
        y = fit_axis("Y", observations["Y"], limit)
    except (TypeError, ValueError) as error:
        raise SystemExit(f"Conversion NOT accepted: {error}") from error
    output = args.output_directory.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    report = output / f"pgm1se-conversion-fit-{stamp}.json"
    candidate = output / f"pgm1se-conversion-snippet-{stamp}.toml"
    report.write_text(
        json.dumps(
            {
                "status": "fit_passed_software_checks_not_hardware_certified",
                "created": datetime.now().astimezone().isoformat(),
                "acceptance_limit_mrad": limit,
                "warning": "Small-range affine extrapolation only; do not test by commanding +/-10 mrad endpoints.",
                "x": fit_data(x),
                "y": fit_data(y),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    candidate.write_text(snippet(x, y, args.evidence), encoding="utf-8")
    print(
        "\nSoftware fit checks passed; this is not independent hardware certification."
    )
    for fit in (x, y):
        print(
            f"{fit.axis}: slope={fit.slope_counts_per_mrad:.6f} counts/mrad, "
            f"zero={fit.zero_count:.3f}, endpoints=({fit.raw_minus_10}, {fit.raw_plus_10}), "
            f"max residual={fit.max_abs_residual_mrad:.6g} mrad, {fit.direction}"
        )
    print(f"Review report: {report}")
    print(f"Candidate TOML: {candidate}")
    print("The instrument configuration was NOT edited.")


if __name__ == "__main__":
    main()
