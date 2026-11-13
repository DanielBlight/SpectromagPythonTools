"""Offline map loading and exclusive PNG export. No instrument imports."""

import csv
import json
import math
from itertools import pairwise
from pathlib import Path


def finite(value, name):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def read_map(path):
    """Read project CSV metadata/rows; retain partial maps as masked cells."""
    path = Path(path)
    if path.stat().st_size > 100_000_000:
        raise ValueError("CSV exceeds the 100 MB viewer limit")
    comments, lines = [], []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for line in stream:
            if line.startswith("# "):
                comments.append(json.loads(line[2:]))
            elif not line.startswith("#"):
                lines.append(line)
    metadata = next((c for c in comments if "xs" in c and "ys" in c), None)
    if metadata is None:
        raise ValueError("CSV is missing map metadata (xs/ys)")
    if metadata.get("position_unit") != "mrad":
        raise ValueError("Viewer requires mirror-angle coordinates in mrad")
    xs = tuple(finite(v, "X coordinate") for v in metadata["xs"])
    ys = tuple(finite(v, "Y coordinate") for v in metadata["ys"])
    for axis in (xs, ys):
        if not 1 <= len(axis) <= 100 or len(set(axis)) != len(axis):
            raise ValueError("Require 1..100 distinct coordinates per axis")
        if len(axis) > 1:
            step = axis[1] - axis[0]
            if not all(
                math.isclose(b - a, step, rel_tol=1e-6, abs_tol=1e-12)
                for a, b in pairwise(axis)
            ):
                raise ValueError("Viewer requires uniformly spaced map axes")
    reader = csv.DictReader(lines)
    required = {"row", "column", "requested_x", "requested_y", "mean_current_a"}
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError("CSV is missing required map columns")
    cells = {}
    for record in reader:
        row, column = int(record["row"]), int(record["column"])
        if not 0 <= row < len(ys) or not 0 <= column < len(xs):
            raise ValueError("Pixel index outside map bounds")
        if (row, column) in cells:
            raise ValueError("Duplicate pixel in CSV")
        for actual, expected in (
            (record["requested_x"], xs[column]),
            (record["requested_y"], ys[row]),
        ):
            if not math.isclose(
                finite(actual, "requested coordinate"),
                expected,
                rel_tol=1e-8,
                abs_tol=1e-10,
            ):
                raise ValueError("CSV pixel coordinates disagree with metadata")
        cells[row, column] = finite(record["mean_current_a"], "current")
    footer = next((c for c in reversed(comments) if "status" in c), {})
    status = footer.get("status", "footer missing / incomplete")
    if status == "complete" and len(cells) != len(xs) * len(ys):
        status = "incomplete: missing pixels despite complete footer"
    return xs, ys, cells, status


def pixel_at(xs, ys, x, y):
    if x is None or y is None or not math.isfinite(x) or not math.isfinite(y):
        return None

    def index(axis, value):
        step = axis[1] - axis[0] if len(axis) > 1 else 1.0
        offset = (value - axis[0]) / step
        if offset < -0.5 or offset >= len(axis) - 0.5:
            return None
        return math.floor(offset + 0.5)

    column, row = index(xs, x), index(ys, y)
    return None if row is None or column is None else (row, column)


def save_png(figure, path):
    """Never overwrite a PNG; remove only this newly created file on failure."""
    path = Path(path)
    if path.suffix.lower() != ".png":
        raise ValueError("Choose a .png filename")
    with path.open("xb") as stream:
        try:
            figure.savefig(stream, format="png", dpi=160)
        except BaseException:
            stream.close()
            path.unlink(missing_ok=True)
            raise
    return path


# Descriptive aliases; preserve existing imports and object identity.
load_map_for_display = read_map
