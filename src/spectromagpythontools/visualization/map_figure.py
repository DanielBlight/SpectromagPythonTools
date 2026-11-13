"""Reusable scientific map display. No instrument imports or pyplot state."""

import math

import numpy as np
from matplotlib import colormaps
from matplotlib.colors import Normalize, TwoSlopeNorm
from matplotlib.figure import Figure
from matplotlib.ticker import EngFormatter


def edges(values):
    values = np.asarray(values, dtype=float)
    if values.size == 1:
        return values[0] - 0.5, values[0] + 0.5
    step = values[1] - values[0]
    return values[0] - step / 2, values[-1] + step / 2


class MapFigure:
    def __init__(self):
        self.figure = Figure(figsize=(5.5, 4.5), dpi=100, layout="constrained")
        self.axes = self.figure.add_subplot()
        self.image = self.axes.imshow(
            np.ma.masked_all((1, 1)),
            origin="lower",
            interpolation="nearest",
            aspect="equal",
            cmap="inferno",
            vmin=0,
            vmax=1,
        )
        self.axes.set_xlabel("X mirror angle (mrad)")
        self.axes.set_ylabel("Y mirror angle (mrad)")
        self.colorbar = self.figure.colorbar(self.image, ax=self.axes)
        self.colorbar.set_label("Current (A)")
        self.colorbar.formatter = EngFormatter(unit="A")
        self.colorbar.update_ticks()

    def update(self, xs, ys, cells, cmap="inferno", mode="Auto", limits=None):
        data = np.full((len(ys), len(xs)), np.nan)
        for (row, column), value in cells.items():
            if 0 <= row < len(ys) and 0 <= column < len(xs) and math.isfinite(value):
                data[row, column] = value
        finite = data[np.isfinite(data)]
        if mode == "Fixed":
            if (
                limits is None
                or not all(math.isfinite(v) for v in limits)
                or limits[0] >= limits[1]
            ):
                raise ValueError(
                    "Fixed limits must be finite, with minimum < maximum (in A)"
                )
            lo, hi = limits
        elif finite.size:
            lo, hi = float(finite.min()), float(finite.max())
        else:
            lo, hi = 0.0, 1.0
        if lo == hi:
            padding = max(abs(lo) * 0.01, 1e-15)
            lo, hi = lo - padding, hi + padding
        if mode == "Zero-centred":
            span = max(abs(lo), abs(hi), 1e-15)
            norm = TwoSlopeNorm(vmin=-span, vcenter=0, vmax=span)
            cmap = "RdBu_r"
        else:
            norm = Normalize(lo, hi)
        palette = colormaps[cmap].with_extremes(bad="#b8b8b8")
        self.image.set_cmap(palette)
        self.image.set_norm(norm)
        self.image.set_data(np.ma.masked_invalid(data))
        x0, x1 = edges(xs)
        y0, y1 = edges(ys)
        self.image.set_extent((x0, x1, y0, y1))
        self.axes.set_xlim(x0, x1)
        self.axes.set_ylim(y0, y1)
        self.colorbar.update_normal(self.image)
        self.colorbar.formatter = EngFormatter(unit="A")
        self.colorbar.update_ticks()
