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


class MapPlot:
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk

        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

        self.model = MapFigure()
        self.last = ((0,), (0,), {})
        self.cmap = tk.StringVar(value="inferno")
        self.mode = tk.StringVar(value="Auto")
        self.minimum = tk.StringVar(value="0")
        self.maximum = tk.StringVar(value="1e-9")
        self.message = tk.StringVar(
            value="Grey = not acquired; Auto rescales as data arrive"
        )
        controls = ttk.Frame(parent)
        controls.pack(fill="x")
        for label, variable, choices in (
            (
                "Colours",
                self.cmap,
                (
                    "inferno",
                    "magma",
                    "viridis",
                    "plasma",
                    "cividis",
                    "turbo",
                    "gray",
                    "coolwarm",
                    "RdBu_r",
                ),
            ),
            ("Scale", self.mode, ("Auto", "Fixed", "Zero-centred")),
        ):
            ttk.Label(controls, text=label).pack(side="left")
            box = ttk.Combobox(
                controls,
                textvariable=variable,
                values=choices,
                state="readonly",
                width=12,
            )
            box.pack(side="left", padx=3)
            box.bind("<<ComboboxSelected>>", lambda event: self.refresh())
        fixed = ttk.Frame(parent)
        fixed.pack(fill="x")
        for label, variable in (("Min (A)", self.minimum), ("Max (A)", self.maximum)):
            ttk.Label(fixed, text=label).pack(side="left")
            entry = ttk.Entry(fixed, textvariable=variable, width=12)
            entry.pack(side="left", padx=3)
            entry.bind("<Return>", lambda event: self.refresh())
        ttk.Button(fixed, text="Apply", command=self.refresh).pack(side="left")
        ttk.Label(parent, textvariable=self.message, wraplength=500).pack(fill="x")
        self.canvas = FigureCanvasTkAgg(self.model.figure, master=parent)
        self.canvas.get_tk_widget().pack(fill="both", expand=True)
        self.hover_text = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.hover_text, wraplength=650).pack(fill="x")
        self.canvas.mpl_connect("motion_notify_event", self.hover)
        self.canvas.mpl_connect(
            "figure_leave_event", lambda event: self.hover_text.set("")
        )
        self.canvas.draw_idle()

    def refresh(self):
        try:
            limits = (
                (float(self.minimum.get()), float(self.maximum.get()))
                if self.mode.get() == "Fixed"
                else None
            )
            self.model.update(
                *self.last, cmap=self.cmap.get(), mode=self.mode.get(), limits=limits
            )
        except (ValueError, OverflowError) as exc:
            self.message.set(str(exc))
            return
        self.message.set(
            "Grey = not acquired; zero-centred uses a diverging palette"
            if self.mode.get() == "Zero-centred"
            else "Grey = not acquired; fixed limits are in A"
        )
        self.canvas.draw_idle()

    def update(self, xs, ys, cells):
        self.last = (xs, ys, cells)
        self.refresh()

    def hover(self, event):
        from spectromagpythontools.gui.map_files import pixel_at

        xs, ys, cells = self.last
        pixel = (
            pixel_at(xs, ys, event.xdata, event.ydata)
            if event.inaxes is self.model.axes
            else None
        )
        if pixel is None:
            self.hover_text.set("")
            return
        row, column = pixel
        value = cells.get(pixel)
        current = "not acquired" if value is None else f"I={value:.6g} A"
        self.hover_text.set(
            f"Row {row + 1}, column {column + 1} | X={xs[column]:.6g} mrad, Y={ys[row]:.6g} mrad | {current}"
        )
