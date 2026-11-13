"""Tk adapter for the reusable headless map figure."""

from spectromagpythontools.visualization.map_figure import MapFigure
from spectromagpythontools.visualization.map_figure import edges


__all__ = ["MapFigure", "MapPlot", "edges"]


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
        from spectromagpythontools.data.map_files import pixel_at

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
