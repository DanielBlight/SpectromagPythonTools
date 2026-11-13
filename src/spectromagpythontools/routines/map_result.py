"""Script-friendly results; the acquisition engine and CSV format are unchanged."""

from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

from spectromagpythontools.routines.photocurrent_map import scan_map


@dataclass(frozen=True)
class MapResult:
    output_csv: Path
    metadata: dict
    footer: dict
    columns: tuple
    records: tuple

    @property
    def xs(self):
        return tuple(self.metadata["xs"])

    @property
    def ys(self):
        return tuple(self.metadata["ys"])

    @property
    def complete(self):
        return self.footer.get("status") == "complete"

    def to_dataframe(self):
        """CSV columns and acquisition order, with metadata in DataFrame.attrs."""
        import pandas as pd

        frame = pd.DataFrame.from_records(self.records, columns=self.columns)
        frame.attrs.update(
            metadata=self.metadata.copy(),
            footer=self.footer.copy(),
            output_csv=str(self.output_csv),
        )
        return frame

    def to_numpy(self):
        """Mean current in A; shape (len(ys), len(xs)); missing pixels are NaN."""
        import numpy as np

        image = np.full((len(self.ys), len(self.xs)), np.nan, dtype=float)
        for pixel in self.records:
            image[pixel["row"], pixel["column"]] = pixel["mean_current_a"]
        return image


def load_map_result(path, *, allow_partial=False):
    """Load a project CSV without hardware; reject partial maps by default."""
    path = Path(path)
    with path.open(encoding="utf-8-sig", newline="") as stream:
        lines = list(stream)
    comments = [json.loads(line[1:].strip()) for line in lines if line.startswith("#")]
    if not comments or not isinstance(comments[0], dict):
        raise ValueError("Missing map metadata")
    metadata = comments[0]
    for key in ("xs", "ys"):
        values = metadata.get(key)
        if not isinstance(values, list) or not values:
            raise ValueError(f"Missing or empty {key} axis")
        if any(
            isinstance(v, bool)
            or not isinstance(v, (int, float))
            or not math.isfinite(v)
            for v in values
        ):
            raise ValueError(f"Invalid {key} axis")
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate {key} axis")
    xs, ys = metadata["xs"], metadata["ys"]
    if len(xs) * len(ys) > 1_000_000:
        raise ValueError("Map exceeds 1,000,000 pixels")
    reader = csv.DictReader(line for line in lines if not line.startswith("#"))
    columns = tuple(reader.fieldnames or ())
    if not {"row", "column", "requested_x", "requested_y", "mean_current_a"}.issubset(
        columns
    ):
        raise ValueError("Missing required map columns")
    records, seen = [], set()
    for raw in reader:
        pixel = {}
        for key, value in raw.items():
            if key is None or value is None:
                raise ValueError("Malformed CSV row")
            if key in ("row", "column"):
                pixel[key] = int(value)
            elif key == "readings_json":
                pixel[key] = value
            else:
                pixel[key] = None if value == "" else float(value)
        row, column = pixel["row"], pixel["column"]
        if not (0 <= row < len(ys) and 0 <= column < len(xs)):
            raise ValueError("Pixel index outside map axes")
        if (row, column) in seen:
            raise ValueError("Duplicate pixel index")
        seen.add((row, column))
        if pixel["requested_x"] != xs[column] or pixel["requested_y"] != ys[row]:
            raise ValueError("Pixel coordinates disagree with metadata")
        current = pixel["mean_current_a"]
        if current is None or not math.isfinite(current):
            raise ValueError("Invalid mean current")
        records.append(pixel)
    footer = next(
        (c for c in reversed(comments[1:]) if isinstance(c, dict) and "status" in c), {}
    )
    complete = footer.get("status") == "complete"
    if complete and (
        len(records) != len(xs) * len(ys) or footer.get("pixels") != len(records)
    ):
        raise ValueError("Completion footer disagrees with pixel count")
    if not complete and not allow_partial:
        raise ValueError("Map is partial; use allow_partial=True to inspect it")
    return MapResult(path, metadata, footer, columns, tuple(records))


def scan_map_result(smu, *, xs, ys, move_and_wait, output_csv, **kwargs):
    """Run the existing engine and return MapResult; errors preserve partial CSV.

    Caller connects/configures instruments and prepares closed-loop motion.
    Existing scan_map callers continue to receive an integer pixel count.
    """
    scan_map(
        smu, xs=xs, ys=ys, move_and_wait=move_and_wait, output_csv=output_csv, **kwargs
    )
    return load_map_result(output_csv)
