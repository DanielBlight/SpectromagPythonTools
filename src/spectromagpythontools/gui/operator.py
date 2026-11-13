"""Operator-facing desktop. Engineering values live in the selected profile."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from spectromagpythontools.configuration import (
    axis_values,
    integer,
    load_config,
    measurement,
    numeric,
)
from spectromagpythontools.gui.commissioning import (
    Application as CommissioningApplication,
)
from spectromagpythontools.gui.control import HardwareWorker, Session
from spectromagpythontools.gui.operator_control import OperatorSession
from spectromagpythontools.gui.operator_profile import (
    PROFILE_FIELDS,
    operation_config,
    validate_profile,
)
from spectromagpythontools.gui.preferences import FIELDS, load_preferences


class Application(CommissioningApplication):
    def __init__(self, root, config_path, *, worker=None):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.root, self.tk, self.ttk = root, tk, ttk
        self.dialog, self.messagebox = filedialog, messagebox
        self.worker = worker
        self.path = Path(config_path).expanduser().resolve()
        self.project = self.path.parent.parent
        self.profile = None
        self.values = {}
        self._preferences_loading = True
        self._preferences_after = None
        self._preferences_traces = []
        self.buttons, self.editors, self.mirror_editors = [], [], []
        self.connected = self.prepared = self.busy = self.closing = False
        self.shutdown_sent = False
        self.cells = {}
        self.grid_shape, self.grid_axes = (1, 1), ((0,), (0,))
        self.position_text = tk.StringVar(
            value="Connect instruments to read mirror position"
        )
        self.status = tk.StringVar(
            value="Not connected. Select a profile, then Connect instruments."
        )
        self.progress_text = tk.StringVar(value="No map acquired")
        self.config_path = tk.StringVar(value=str(self.path))
        self.remote, self.serpentine = (
            tk.BooleanVar(value=False),
            tk.BooleanVar(value=True),
        )
        self.log_file = None
        self.connection_summary = tk.StringVar(
            value="Mirror: disconnected | Keithley: not checked"
        )
        root.title("Spectromag | Mirror control and current maps")
        root.geometry("1180x880")
        root.minsize(950, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Instrument profile:").pack(side="left")
        ttk.Label(top, textvariable=self.config_path, wraplength=650).pack(
            side="left", fill="x", expand=True
        )
        self.button(top, "Choose profile", self.browse_config, role="profile")
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=5)
        self.button(
            actions,
            "Connect instruments",
            lambda: self.dispatch("connect"),
            role="connect",
        )
        self.button(
            actions, "Disconnect", lambda: self.dispatch("disconnect"), role="connected"
        )
        ttk.Label(outer, textvariable=self.connection_summary).pack(fill="x", pady=4)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        mirror, mapping = (ttk.Frame(self.tabs, padding=10) for _ in range(2))
        self.tabs.add(mirror, text="Move mirror")
        self.tabs.add(mapping, text="Run map")
        self.mirror_tab(mirror)
        self.map_tab(mapping)
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=5)
        ttk.Label(bottom, textvariable=self.status, wraplength=900).pack(
            side="left", fill="x", expand=True
        )
        self.stop_button = ttk.Button(bottom, text="Stop", command=self.stop)
        self.stop_button.pack(side="right")
        self.log_widget = tk.Text(outer, height=6, state="disabled", wrap="word")
        self._log_visible = False
        ttk.Button(outer, text="Show / hide diagnostics", command=self.toggle_log).pack(
            anchor="w"
        )
        if self.worker is None:
            self.worker = HardwareWorker(OperatorSession)
        self._preference_defaults = {
            key: var.get() for key, var in self.values.items() if key in FIELDS
        }
        self.load()
        self._preferences_loading = False
        self._watch_preferences()
        self.update_controls()
        root.after(100, self.poll)

    def toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_widget.pack(fill="x")
        else:
            self.log_widget.pack_forget()

    def new_output(self):
        directory = Path(self.values["output_csv"].get()).expanduser().parent
        if not directory.is_dir():
            directory = self.project / "data"
        self.values["output_csv"].set(
            str(directory / f"map-{datetime.now():%Y%m%d-%H%M%S-%f}.csv")
        )

    def load(self):
        if self.connected or self.busy:
            self.messagebox.showerror(
                "Disconnect first", "Disconnect instruments before changing profile."
            )
            return
        try:
            candidate = Path(self.config_path.get()).expanduser().resolve()
            profile = load_config(candidate)
            validate_profile(profile)
            self._cancel_preferences_save()
            self._preferences_loading = True
            self.profile, self.path = profile, candidate
            self.project = candidate.parent.parent
            for key, value in self._preference_defaults.items():
                self.values[key].set(value)
            mapping = profile.get("photocurrent_map", {})
            aliases = {"pixel_settle_s": "settle_s", "samples_per_pixel": "samples"}
            for key, value in mapping.items():
                key = aliases.get(key, key)
                if key in self.values:
                    self.values[key].set(str(value))
            candidates = [
                numeric(mapping[key], key, 1e-10, 0.1)
                for key in ("max_current_a", "current_limit_a")
                if mapping.get(key) not in (None, "")
            ]
            if candidates:
                self.values["max_current_a"].set(str(min(candidates)))
            self.remote.set(profile["keithley"].get("remote_sense", False))
            self.serpentine.set(mapping.get("serpentine", True))
            (self.project / "data").mkdir(exist_ok=True)
            self.values["output_csv"].set(str(self.project / "data" / "map.csv"))
            self.new_output()
            if self.log_file is None:
                (self.project / "logs").mkdir(exist_ok=True)
                self.log_file = (
                    self.project
                    / "logs"
                    / f"operator-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
                ).open("x", encoding="utf-8")
            self._restore_preferences()
            self.log(f"Profile loaded: {self.path}; no hardware connected")
        except Exception as exc:  # noqa: BLE001
            self.profile = None
            self.log(f"Profile unavailable: {exc}")
            self.messagebox.showerror("Instrument profile", str(exc))
        finally:
            self._preferences_loading = False

    def _restore_preferences(self):
        try:
            data = load_preferences(self.path)
            for key, value in data["values"].items():
                if key in self.values and key not in PROFILE_FIELDS:
                    self.values[key].set(value)
            for key, var in (
                ("remote_sense", self.remote),
                ("serpentine", self.serpentine),
            ):
                if key in data["flags"]:
                    var.set(data["flags"][key])
        except (OSError, ValueError, TypeError) as exc:
            self.log(f"Saved settings ignored: {exc}")

    def config(self, *, electrical=True):
        if self.profile is None:
            raise ValueError("Load a valid instrument profile first")
        values = {key: var.get().strip() for key, var in self.values.items()}
        if not electrical:
            values["bias_v"] = "0"
        return operation_config(
            self.profile,
            values,
            remote=self.remote.get(),
            serpentine=self.serpentine.get(),
        )

    def dispatch(self, operation, overrides=None):
        try:
            config = self.config(electrical=operation in {"map", "read_current"})
            if overrides:
                config.update(overrides)
            self._save_preferences()
            self.worker.submit(operation, config)
            self.busy = True
            self.status.set(f"Running {operation}; waiting for instrument I/O")
            self.log(f"Started {operation}")
            self.update_controls()
        except Exception as exc:  # noqa: BLE001
            self.messagebox.showerror("Cannot start", str(exc))

    def centre(self):
        self.dispatch("move", {"x": 0, "y": 0})

    def update_controls(self):
        super().update_controls()
        self.connection_summary.set(
            "Mirror: ready | Keithley: checked on Connect; session reopens for each acquisition"
            if self.prepared
            else "Mirror: connected, reconnect before moving/mapping | Keithley: recheck on Connect"
            if self.connected
            else "Mirror: disconnected | Keithley: not checked"
        )
        for widget, role in self.buttons:
            if role == "profile":
                widget.configure(
                    state="disabled"
                    if self.busy or self.closing or self.connected
                    else "normal"
                )
            elif role == "connect" and self.profile is None:
                widget.configure(state="disabled")

    def close(self):
        if self.closing:
            return
        self._save_preferences()
        self.closing = True
        self.worker.cancel.set()
        self.status.set("Closing: waiting for acquisition cleanup and disconnection")
        self.update_controls()

    def mirror_tab(self, frame):
        actions = self.ttk.Frame(frame)
        actions.pack(fill="x")
        self.button(
            actions,
            "Refresh position",
            lambda: self.dispatch("inspect"),
            role="connected",
        )
        self.ttk.Label(
            frame, textvariable=self.position_text, font=("Segoe UI", 14)
        ).pack(pady=14)
        form = self.ttk.Frame(frame)
        form.pack()
        for row, (key, label, default) in enumerate(
            (
                ("x", "X target (mrad)", 0),
                ("y", "Y target (mrad)", 0),
                ("jog_step", "Jog step (mrad)", 0.01),
            )
        ):
            self.field(form, row, key, label, default)
        moves = self.ttk.Frame(frame)
        moves.pack(pady=10)
        self.button(moves, "Go to X/Y", lambda: self.dispatch("move"), role="prepared")
        self.button(moves, "Move to 0,0", self.centre, role="prepared")
        jog = self.ttk.Frame(frame)
        jog.pack()
        for label, dx, dy in (
            ("X -", -1, 0),
            ("Y +", 0, 1),
            ("Y -", 0, -1),
            ("X +", 1, 0),
        ):
            self.button(jog, label, lambda a=dx, b=dy: self.jog(a, b), role="prepared")

    def map_tab(self, frame):
        # Scroll the settings column so controls remain accessible on laptop displays.
        settings_host = self.ttk.Frame(frame)
        settings_host.pack(side="left", fill="y", padx=5)
        settings_canvas = self.tk.Canvas(settings_host, width=440, highlightthickness=0)
        settings_scroll = self.ttk.Scrollbar(
            settings_host, orient="vertical", command=settings_canvas.yview
        )
        settings_canvas.configure(yscrollcommand=settings_scroll.set)
        settings_scroll.pack(side="right", fill="y")
        settings_canvas.pack(side="left", fill="y", expand=True)
        settings = self.ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window(
            (0, 0), window=settings, anchor="nw"
        )
        settings.bind(
            "<Configure>",
            lambda event: settings_canvas.configure(
                scrollregion=settings_canvas.bbox("all")
            ),
        )
        settings_canvas.bind(
            "<Configure>",
            lambda event: settings_canvas.itemconfigure(
                settings_window, width=event.width
            ),
        )
        form = self.ttk.Frame(settings)
        form.pack(fill="x")
        fields = (
            ("x_start", "X start (mrad)", -0.05),
            ("x_end", "X end (mrad)", 0.05),
            ("nx", "X points (1..100)", 5),
            ("y_start", "Y start (mrad)", -0.05),
            ("y_end", "Y end (mrad)", 0.05),
            ("ny", "Y points (1..100)", 5),
            ("bias_v", "Bias (V)", 0),
            ("max_current_a", "Max current / compliance (A)", ""),
            ("nplc", "Integration (NPLC)", 1),
            ("current_range_a", "Current range (A), blank=auto", ""),
            ("samples", "Readings per pixel", 3),
            ("settle_s", "Extra pixel settling (s)", 0.1),
            ("measurement_label", "Label: light/dark/unclassified", "unclassified"),
        )
        advanced = self.ttk.Frame(settings)
        advanced_form = self.ttk.Frame(advanced)
        advanced_form.pack(fill="x")
        advanced_button = self.ttk.Button(
            settings, text="Show advanced measurement settings"
        )
        advanced_button.pack(fill="x", pady=4)
        shown = [False]

        def toggle():
            shown[0] = not shown[0]
            if shown[0]:
                advanced.pack(fill="x", after=advanced_button)
            else:
                advanced.pack_forget()
            advanced_button.configure(
                text="Hide advanced measurement settings"
                if shown[0]
                else "Show advanced measurement settings"
            )

        advanced_button.configure(command=toggle)
        advanced_keys = {"nplc", "current_range_a", "samples", "settle_s"}
        rows = {False: 0, True: 0}
        for key, label, default in fields:
            is_advanced = key in advanced_keys
            self.field(
                advanced_form if is_advanced else form,
                rows[is_advanced],
                key,
                label,
                default,
                width=18,
            )
            rows[is_advanced] += 1
        for text, variable in (
            ("Remote (4-wire) sense", self.remote),
            ("Serpentine scan", self.serpentine),
        ):
            check = self.ttk.Checkbutton(advanced, text=text, variable=variable)
            check.pack(anchor="w")
            self.editors.append(check)
        output = self.ttk.Frame(settings)
        output.pack(fill="x", pady=4)
        self.values["output_csv"] = self.tk.StringVar(value="")
        entry = self.ttk.Entry(output, textvariable=self.values["output_csv"], width=26)
        entry.pack(side="left", fill="x", expand=True)
        self.editors.append(entry)
        self.button(output, "CSV...", self.choose_output)
        actions = self.ttk.Frame(settings)
        actions.pack(fill="x")
        self.button(actions, "Read current once", self.read_current, role="prepared")
        self.button(actions, "Start map", self.start_map, role="prepared")
        display = self.ttk.Frame(frame)
        display.pack(side="left", fill="both", expand=True, padx=8)
        from spectromagpythontools.gui.map_plot import MapPlot

        self.map_plot = MapPlot(display)
        file_actions = self.ttk.Frame(display)
        file_actions.pack(fill="x")
        self.save_png_enabled = self.tk.BooleanVar(value=False)
        png_check = self.ttk.Checkbutton(
            file_actions,
            text="Save PNG after completed map",
            variable=self.save_png_enabled,
        )
        png_check.pack(side="left")
        self.editors.append(png_check)
        self.button(file_actions, "Load map CSV", self.load_map_csv)
        self.button(file_actions, "Save PNG now", self.save_map_png)

        self.scale_text = self.tk.StringVar(
            value="Grey = not acquired; colourbar shows total current"
        )
        self.ttk.Label(display, textvariable=self.scale_text, wraplength=450).pack(
            pady=5
        )
        self.progress = self.ttk.Progressbar(display, maximum=1)
        self.progress.pack(fill="x")
        self.ttk.Label(display, textvariable=self.progress_text, wraplength=450).pack(
            pady=5
        )

    def read_current(self):
        try:
            config = self.config()
            measurement(config)
            integer(config["samples"], "samples", 1, 1000)
            numeric(config["settle_s"], "settle", 0, 60)
        except (ValueError, TypeError) as exc:
            self.messagebox.showerror("Current settings", str(exc))
            return
        self.dispatch("read_current")

    def start_map(self):
        try:
            if not self.prepared:
                raise ValueError("Connect instruments first")
            if Path(self.values["output_csv"].get()).exists():
                self.new_output()
            config = self.config()
            measurement(config)
            Session.motion(config)
            xs = axis_values(config["x_start"], config["x_end"], config["nx"])
            ys = axis_values(config["y_start"], config["y_end"], config["ny"])
            if Path(config["output_csv"]).exists():
                self.new_output()
                config["output_csv"] = self.values["output_csv"].get()
                if Path(config["output_csv"]).exists():
                    raise FileExistsError(
                        "Could not allocate a new output CSV filename"
                    )
        except (ValueError, TypeError, OSError) as exc:
            self.messagebox.showerror("Map settings", str(exc))
            return
        self.cells.clear()
        self.grid_shape = (len(xs), len(ys))
        self.grid_axes = (xs, ys)
        self.progress.configure(maximum=len(xs) * len(ys), value=0)
        self.progress_text.set("Starting; no pixels acquired")
        self.draw_map()
        self.dispatch("map")

    def load_map_csv(self):
        if self.busy or self.closing:
            self.messagebox.showerror(
                "Map viewer", "Wait for the current operation to finish."
            )
            return
        path = self.dialog.askopenfilename(filetypes=[("Map CSV", "*.csv")])
        if not path:
            return
        try:
            from spectromagpythontools.gui.map_files import read_map

            xs, ys, cells, status = read_map(path)
            self.grid_axes, self.grid_shape = (xs, ys), (len(xs), len(ys))
            self.cells = cells
            self.progress.configure(maximum=len(xs) * len(ys), value=len(cells))
            self.progress_text.set(
                f"Loaded {Path(path).name}: {len(cells)}/{len(xs) * len(ys)} pixels; {status}"
            )
            self.draw_map()
            self.log(f"Displayed CSV: {path}; {status}; no hardware commands sent")
        except (OSError, ValueError, TypeError, KeyError, OverflowError) as exc:
            self.messagebox.showerror("Cannot load map", str(exc))

    def save_map_png(self):
        if self.busy or self.closing:
            return
        path = self.dialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG image", "*.png")]
        )
        if path:
            self._export_map_png(path)

    def _export_map_png(self, path):
        try:
            from spectromagpythontools.gui.map_files import save_png

            saved = save_png(self.map_plot.model.figure, path)
            self.log(f"Map PNG saved: {saved}")
        except (OSError, ValueError, RuntimeError) as exc:
            self.log(f"PNG not saved; measurement CSV is unchanged: {exc}")
            self.messagebox.showerror("PNG not saved", str(exc))

    def map_completed(self, result):
        if self.save_png_enabled.get() and result.get("output_csv"):
            self.draw_map()
            self._export_map_png(Path(result["output_csv"]).with_suffix(".png"))


def launch(config_path, tab="mirror"):
    import tkinter as tk

    root = tk.Tk()
    app = Application(root, config_path)
    app.tabs.select(0 if tab == "mirror" else 1)
    try:
        root.mainloop()
    finally:
        app.worker.request_shutdown()
        app.worker.wait_stopped()
        if app.log_file is not None and not app.log_file.closed:
            app.log_file.close()
