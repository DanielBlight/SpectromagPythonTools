"""Small Tk desktop application. Importing does not create a window or hardware."""

from __future__ import annotations

import json
import queue
from datetime import datetime
from pathlib import Path

from spectromagpythontools.configuration import (
    axis_values,
    integer,
    load_config,
    measurement,
    numeric,
)
from spectromagpythontools.gui.control import HardwareWorker, Session
from spectromagpythontools.gui.preferences import (
    FIELDS,
    load_preferences,
    save_preferences,
)


class Application:
    def __init__(self, root, config_path, *, worker=None):
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk

        self.root, self.tk, self.ttk = root, tk, ttk
        self.dialog, self.messagebox = filedialog, messagebox
        self.worker = worker
        self.path = Path(config_path).expanduser().resolve()
        self.project = self.path.parent.parent
        self.values = {}
        self._preferences_loading = True
        self._preferences_after = None
        self._preferences_traces = []
        self.buttons = []
        self.editors = []
        self.mirror_editors = []
        self.connected = self.prepared = self.busy = self.closing = False
        self.shutdown_sent = False
        self.cells = {}
        self.grid_shape = (1, 1)
        self.grid_axes = ((0,), (0,))
        self.position_text = tk.StringVar(value="Mirror not connected")
        self.status = tk.StringVar(value="Offline: no hardware connection")
        self.progress_text = tk.StringVar(value="No map acquired")
        self.config_path = tk.StringVar(value=str(self.path))
        self.remote = tk.BooleanVar(value=False)
        self.serpentine = tk.BooleanVar(value=True)
        self.ack = tk.BooleanVar(value=False)
        self.cal_verified = tk.BooleanVar(value=False)
        self.log_file = None
        root.title("SpectromagPythonTools | Mirror and photocurrent mapping")
        root.geometry("1120x860")
        root.minsize(900, 700)
        root.protocol("WM_DELETE_WINDOW", self.close)
        outer = ttk.Frame(root, padding=10)
        outer.pack(fill="both", expand=True)
        top = ttk.Frame(outer)
        top.pack(fill="x")
        ttk.Label(top, text="Instrument configuration:").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.config_path)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        self.editors.append(entry)
        self.button(top, "Browse", self.browse_config)
        self.button(top, "Load", self.load)
        ttk.Label(
            outer,
            text=(
                "Mechanical mirror angles: +/-10 mrad maximum. No automatic laser/shutter control. "
                "Stop is cooperative, NOT an emergency stop."
            ),
            wraplength=1050,
        ).pack(fill="x", pady=6)
        self.tabs = ttk.Notebook(outer)
        self.tabs.pack(fill="both", expand=True)
        setup, mirror, mapping = (ttk.Frame(self.tabs, padding=10) for _ in range(3))
        self.tabs.add(setup, text="Connections / calibration")
        self.tabs.add(mirror, text="Mirror control")
        self.tabs.add(mapping, text="Photocurrent map")
        self.setup_tab(setup)
        self.mirror_tab(mirror)
        self.map_tab(mapping)
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=5)
        ttk.Label(bottom, textvariable=self.status, wraplength=850).pack(
            side="left", fill="x", expand=True
        )
        self.stop_button = ttk.Button(bottom, text="STOP / cancel", command=self.stop)
        self.stop_button.pack(side="right")
        self.log_widget = tk.Text(outer, height=6, state="disabled", wrap="word")
        self.log_widget.pack(fill="x")
        # No worker is started until all widgets exist.
        if self.worker is None:
            self.worker = HardwareWorker()
        self._preference_defaults = {
            key: variable.get()
            for key, variable in self.values.items()
            if key in FIELDS
        }
        self.load()
        self._preferences_loading = False
        self._watch_preferences()
        self.update_controls()
        root.after(100, self.poll)

    def button(self, parent, text, command, *, role="idle"):
        widget = self.ttk.Button(parent, text=text, command=command)
        widget.pack(side="left", padx=3, pady=3)
        self.buttons.append((widget, role))
        return widget

    def field(self, parent, row, key, label, default="", width=24):
        self.ttk.Label(parent, text=label).grid(
            row=row, column=0, sticky="w", padx=4, pady=3
        )
        value = self.tk.StringVar(value=str(default))
        self.values[key] = value
        entry = self.ttk.Entry(parent, textvariable=value, width=width)
        entry.grid(row=row, column=1, sticky="ew", padx=4, pady=3)
        self.editors.append(entry)
        parent.columnconfigure(1, weight=1)
        return entry

    def setup_tab(self, frame):
        left, right = self.ttk.Frame(frame), self.ttk.Frame(frame)
        left.pack(side="left", fill="both", expand=True, padx=5)
        right.pack(side="left", fill="both", expand=True, padx=5)
        mirror_start = len(self.editors)
        self.ttk.Label(left, text="PPC102 USB controller").pack(anchor="w")
        form = self.ttk.Frame(left)
        form.pack(fill="x")
        for row, (key, label, default) in enumerate(
            (
                ("serial", "Serial", "95XXXXXX"),
                ("x_channel", "X channel", 1),
                ("y_channel", "Y channel", 2),
                ("max_abs_mrad", "Software limit (mrad)", 10),
                (
                    "kinesis_directory",
                    "Kinesis directory",
                    r"C:\Program Files\Thorlabs\Kinesis",
                ),
                ("response_wait_s", "SDK response wait (s)", 0.1),
            )
        ):
            self.field(form, row, key, label, default)
        self.ttk.Label(
            left,
            text=(
                "Verified count conversion (optional for raw inspection).\n"
                "Leave blank until confirmed for your matched mount/controller."
            ),
            wraplength=450,
        ).pack(anchor="w", pady=8)
        cal = self.ttk.Frame(left)
        cal.pack(fill="x")
        for row, (key, label) in enumerate(
            (
                ("x_minus", "X raw count at -10 mrad"),
                ("x_plus", "X raw count at +10 mrad"),
                ("y_minus", "Y raw count at -10 mrad"),
                ("y_plus", "Y raw count at +10 mrad"),
                ("evidence", "Conversion evidence / reference"),
            )
        ):
            self.field(cal, row, key, label)
        check = self.ttk.Checkbutton(
            left,
            text="I verified these conversions (not test fixtures)",
            variable=self.cal_verified,
        )
        check.pack(anchor="w")
        self.editors.append(check)
        self.mirror_editors = self.editors[mirror_start:]
        self.ttk.Label(right, text="Keithley USB / VISA").pack(anchor="w")
        form = self.ttk.Frame(right)
        form.pack(fill="x")
        for row, (key, label, default) in enumerate(
            (
                ("resource", "USB VISA resource", "USB0::PLACEHOLDER::INSTR"),
                ("model", "Exact model", "2602B"),
                ("channel", "Channel (a/b)", "a"),
                ("timeout_ms", "VISA timeout (ms)", 5000),
                ("visa_library", "VISA backend (blank=default)", ""),
            )
        ):
            self.field(form, row, key, label, default)
        actions = self.ttk.Frame(right)
        actions.pack(fill="x", pady=5)
        self.button(actions, "List USB VISA", lambda: self.dispatch("resources"))
        self.button(actions, "Identify Keithley", lambda: self.dispatch("identify"))
        self.ttk.Label(
            right,
            text=(
                "Identify uses the driver connection sequence, which forces the selected SMU output off.\n\n"
                "Connection/calibration edits apply to the next connection only; local TOML is not changed. Operational settings are remembered per user. "
                "Disconnect the mirror before changing its connection or calibration.\n\n"
                "Native SDK readbacks are cached. This application cannot certify freshness or "
                "interrupt a blocked native call. Do not use it as a laser safety interlock."
            ),
            wraplength=440,
            justify="left",
        ).pack(anchor="w", pady=12)

    def mirror_tab(self, frame):
        actions = self.ttk.Frame(frame)
        actions.pack(fill="x")
        self.button(
            actions,
            "Connect (no move)",
            lambda: self.dispatch("connect"),
            role="connect",
        )
        self.button(
            actions,
            "Read position / raw state",
            lambda: self.dispatch("inspect"),
            role="connected",
        )
        self.button(actions, "Prepare closed loop", self.prepare, role="connected")
        self.button(
            actions, "Disconnect", lambda: self.dispatch("disconnect"), role="connected"
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
                ("mirror_tolerance_mrad", "Tolerance (mrad)", 0.005),
                ("mirror_stable_s", "Stable interval (s)", 0.2),
                ("mirror_timeout_s", "Move timeout (s)", 10),
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
        self.ttk.Label(
            frame,
            text=(
                "Jog uses current SDK readback, not the displayed target. No keyboard-repeat motion.\n"
                "Preparing closed loop can move the beam. Disconnect/close does NOT park the mirror."
            ),
            wraplength=850,
            justify="center",
        ).pack(pady=16)

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
        for row, (key, label, default) in enumerate(fields):
            self.field(form, row, key, label, default, width=18)
        for text, variable in (
            ("Remote (4-wire) sense", self.remote),
            ("Serpentine scan", self.serpentine),
        ):
            check = self.ttk.Checkbutton(settings, text=text, variable=variable)
            check.pack(anchor="w")
            self.editors.append(check)
        output = self.ttk.Frame(settings)
        output.pack(fill="x", pady=4)
        self.values["output_csv"] = self.tk.StringVar(value="")
        entry = self.ttk.Entry(output, textvariable=self.values["output_csv"], width=26)
        entry.pack(side="left", fill="x", expand=True)
        self.editors.append(entry)
        self.button(output, "CSV...", self.choose_output)
        check = self.ttk.Checkbutton(
            settings,
            text="Beam path checked; SDK cache limitation understood",
            variable=self.ack,
        )
        check.pack(anchor="w")
        self.editors.append(check)
        actions = self.ttk.Frame(settings)
        actions.pack(fill="x")
        self.button(actions, "Read current once", self.read_current)
        self.button(actions, "Start map", self.start_map, role="prepared")
        display = self.ttk.Frame(frame)
        display.pack(side="left", fill="both", expand=True, padx=8)
        self.ttk.Label(
            display, text="Live total current (A), not dark-subtracted"
        ).pack()
        from spectromagpythontools.gui.map_plot import MapPlot

        self.map_plot = MapPlot(display)
        self.scale_text = self.tk.StringVar(
            value="Inferno/magma; grey = not acquired; colourbar shows current"
        )
        self.ttk.Label(display, textvariable=self.scale_text, wraplength=450).pack(
            pady=5
        )
        self.progress = self.ttk.Progressbar(display, maximum=1)
        self.progress.pack(fill="x")
        self.ttk.Label(display, textvariable=self.progress_text, wraplength=450).pack(
            pady=5
        )

    def log(self, text):
        line = f"{datetime.now().isoformat(timespec='seconds')} {text}\n"
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", line)
        if int(self.log_widget.index("end-1c").split(".")[0]) > 2000:
            self.log_widget.delete("1.0", "200.0")
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")
        if self.log_file is not None:
            try:
                self.log_file.write(line)
                self.log_file.flush()
            except OSError:
                self.log_file.close()
                self.log_file = None

    def load(self):
        if self.connected or self.busy:
            self.messagebox.showerror(
                "Disconnect first",
                "Disconnect the mirror and wait for idle before loading configuration.",
            )
            return
        try:
            self._cancel_preferences_save()
            self._preferences_loading = True
            candidate = Path(self.config_path.get()).expanduser().resolve()
            config = load_config(candidate)
            self.path = candidate
            self.project = self.path.parent.parent
            for key, value in self._preference_defaults.items():
                self.values[key].set(value)
            for section in ("pgm1se", "keithley"):
                for key, value in config.get(section, {}).items():
                    if key in self.values:
                        self.values[key].set(str(value))
            for key in ("x_minus", "x_plus", "y_minus", "y_plus", "evidence"):
                self.values[key].set("")
            self.cal_verified.set(False)
            evidence = []
            for axis in ("x", "y"):
                cal = config.get("pgm1se", {}).get(f"{axis}_calibration", {})
                if cal:
                    self.values[f"{axis}_minus"].set(str(cal.get("raw_minus_10", "")))
                    self.values[f"{axis}_plus"].set(str(cal.get("raw_plus_10", "")))
                    evidence.append(str(cal.get("evidence", "")))
            if evidence:
                self.values["evidence"].set(" | ".join(evidence))
                self.cal_verified.set(
                    all(
                        config["pgm1se"].get(f"{a}_calibration", {}).get("verified")
                        is True
                        for a in ("x", "y")
                    )
                )
            mapping = config.get("photocurrent_map", {})
            aliases = {"pixel_settle_s": "settle_s", "samples_per_pixel": "samples"}
            for key, value in mapping.items():
                target = aliases.get(key, key)
                if target in self.values:
                    self.values[target].set(str(value))
            # Migrate old TOML conservatively: use the smaller explicit current
            # ceiling/compliance, rather than silently raising compliance.
            candidates = []
            for key in ("max_current_a", "current_limit_a"):
                if mapping.get(key) not in (None, ""):
                    candidates.append(numeric(mapping[key], key, 1e-10, 0.1))
            if candidates:
                self.values["max_current_a"].set(str(min(candidates)))
            self.serpentine.set(mapping.get("serpentine", True) is True)
            self.remote.set(
                config.get("keithley", {}).get("remote_sense", False) is True
            )
            data = self.project / "data"
            data.mkdir(exist_ok=True)
            self.values["output_csv"].set(
                str(data / f"map-{datetime.now():%Y%m%d-%H%M%S-%f}.csv")
            )
            if self.log_file is None:
                logs = self.project / "logs"
                logs.mkdir(exist_ok=True)
                self.log_file = (
                    logs / f"gui-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
                ).open("x", encoding="utf-8")
            self._restore_preferences()
            self.log(
                f"Loaded {self.path}; operational settings remembered per user. "
                "Local TOML unchanged; no hardware connected."
            )
        except Exception as exc:  # noqa: BLE001
            self.log(f"Configuration not loaded: {exc}")
            self.messagebox.showerror("Configuration", str(exc))
        finally:
            self._preferences_loading = False

    def browse_config(self):
        path = self.dialog.askopenfilename(filetypes=[("TOML configuration", "*.toml")])
        if path:
            self.config_path.set(path)
            self.load()

    def choose_output(self):
        path = self.dialog.asksaveasfilename(
            defaultextension=".csv", filetypes=[("CSV", "*.csv")]
        )
        if path:
            self.values["output_csv"].set(path)

    def config(self):
        values = {key: variable.get().strip() for key, variable in self.values.items()}
        bias = numeric(values["bias_v"], "bias (V)", -20, 20)
        values["max_abs_bias_v"] = abs(bias)
        values["current_limit_a"] = values["max_current_a"]
        values["pgm1se"] = {
            "serial": values["serial"],
            "x_channel": integer(values["x_channel"], "X channel", 1, 2),
            "y_channel": integer(values["y_channel"], "Y channel", 1, 2),
            "max_abs_mrad": numeric(
                values["max_abs_mrad"], "angle limit", 0.000001, 10
            ),
            "kinesis_directory": values["kinesis_directory"],
            "response_wait_s": numeric(
                values["response_wait_s"], "SDK response wait", 0.02, 2
            ),
        }
        for axis in ("x", "y"):
            if values[f"{axis}_minus"] or values[f"{axis}_plus"]:
                values["pgm1se"][f"{axis}_calibration"] = {
                    "raw_minus_10": integer(
                        values[f"{axis}_minus"], "raw -10", -32767, 32767
                    ),
                    "raw_plus_10": integer(
                        values[f"{axis}_plus"], "raw +10", -32767, 32767
                    ),
                    "evidence": values["evidence"],
                    "verified": self.cal_verified.get(),
                }
        values["keithley"] = {
            "resource": values["resource"],
            "model": values["model"].upper(),
            "channel": values["channel"].lower(),
            "timeout_ms": integer(values["timeout_ms"], "VISA timeout", 100, 120000),
            "visa_library": values["visa_library"],
        }
        values["remote_sense"] = self.remote.get()
        values["serpentine"] = self.serpentine.get()
        values["commissioning_ack"] = self.ack.get()
        return values

    def dispatch(self, operation, overrides=None):
        try:
            config = self.config()
            if overrides:
                config.update(overrides)
            self._save_preferences()
            self.worker.submit(operation, config)
            self.busy = True
            self.status.set(
                f"Running {operation}; cancellation waits for in-flight I/O"
            )
            self.log(f"Started {operation}")
            self.update_controls()
        except Exception as exc:  # noqa: BLE001
            self.messagebox.showerror("Cannot start", str(exc))

    def prepare(self):
        if self.messagebox.askyesno(
            "Prepare closed loop",
            (
                "This selects software-only input, enables both channels and can move the beam.\n"
                "Block the laser and confirm the count conversion first. Continue?"
            ),
        ):
            self.dispatch("prepare")

    def centre(self):
        if self.messagebox.askyesno(
            "Move to 0,0",
            "Move the mirror to 0,0 mrad? This is not a power-off or safety position.",
        ):
            self.dispatch("move", {"x": 0, "y": 0})

    def jog(self, dx, dy):
        try:
            step = numeric(self.values["jog_step"].get(), "jog step", 0.000001, 10)
            self.dispatch("move", {"dx": dx * step, "dy": dy * step})
        except (ValueError, TypeError) as exc:
            self.messagebox.showerror("Jog", str(exc))

    def read_current(self):
        try:
            config = self.config()
            measurement(config)
            integer(config["samples"], "samples", 1, 1000)
            numeric(config["settle_s"], "settle", 0, 60)
        except (ValueError, TypeError) as exc:
            self.messagebox.showerror("Current settings", str(exc))
            return
        if self.messagebox.askyesno(
            "Enable Keithley output",
            (
                f"Apply {config['bias_v']} V with {config['current_limit_a']} A compliance, "
                "read current, then disable output?\nConfirm the DUT limits and wiring first."
            ),
        ):
            self.dispatch("read_current")

    def start_map(self):
        try:
            config = self.config()
            measurement(config)
            Session.motion(config)
            xs = axis_values(config["x_start"], config["x_end"], config["nx"])
            ys = axis_values(config["y_start"], config["y_end"], config["ny"])
            if not config["commissioning_ack"]:
                raise ValueError("Check the beam-path / SDK-cache acknowledgement")
            if Path(config["output_csv"]).exists():
                raise FileExistsError(
                    "Choose a NEW output CSV; existing files are never overwritten"
                )
        except (ValueError, TypeError, OSError) as exc:
            self.messagebox.showerror("Map settings", str(exc))
            return
        if not self.messagebox.askyesno(
            "Start angular photocurrent map",
            (
                f"Acquire {len(xs)} x {len(ys)} pixels in mechanical mrad?\n"
                f"Bias {config['bias_v']} V; compliance {config['current_limit_a']} A.\n"
                f"Save to {config['output_csv']}\n\n"
                "The laser/shutter is NOT controlled. Current is NOT dark-subtracted."
            ),
        ):
            return
        self.cells.clear()
        self.grid_shape = (len(xs), len(ys))
        self.grid_axes = (xs, ys)
        self.progress.configure(maximum=len(xs) * len(ys), value=0)
        self.progress_text.set("Starting; no pixels acquired")
        self.draw_map()
        self.dispatch("map")

    def stop(self):
        self.worker.cancel.set()
        self.status.set(
            "Cancel requested; waiting for current I/O and cleanup. Mirror is not parked."
        )
        self.log(
            "Cancel requested. Not an emergency stop; native calls cannot be forcibly interrupted."
        )

    def update_controls(self):
        for widget, role in self.buttons:
            allowed = not self.busy and not self.closing
            if role == "connect":
                allowed = allowed and not self.connected
            elif role == "connected":
                allowed = allowed and self.connected
            elif role == "prepared":
                allowed = allowed and self.prepared
            widget.configure(state="normal" if allowed else "disabled")
        for widget in self.editors:
            widget.configure(
                state="disabled"
                if (
                    self.busy
                    or self.closing
                    or (self.connected and widget in self.mirror_editors)
                )
                else "normal"
            )
        self.stop_button.configure(state="normal" if self.busy else "disabled")

    def draw_map(self):
        plot = getattr(self, "map_plot", None)
        if plot is not None:
            plot.update(*self.grid_axes, self.cells)

    def poll(self):
        redraw = False
        for _ in range(250):
            try:
                kind, payload = self.worker.events.get_nowait()
            except queue.Empty:
                break
            if kind == "pixel":
                self.cells[(payload["row"], payload["column"])] = payload["current_a"]
                self.progress.configure(value=payload["completed"])
                self.progress_text.set(
                    f"{payload['completed']}/{payload['total']} pixels; I={payload['current_a']:.5g} A; "
                    f"readback=({payload['actual_x']:.5g}, {payload['actual_y']:.5g}) mrad"
                )
                redraw = True
            elif kind == "state":
                self.connected, self.prepared = (
                    payload["connected"],
                    payload["prepared"],
                )
            elif kind == "result":
                result = payload["value"] or {}
                if payload["operation"] == "map":
                    completed = getattr(self, "map_completed", None)
                    if callable(completed):
                        completed(result)

                self.log(f"{payload['operation']}: {json.dumps(result, default=str)}")
                if "position" in result:
                    x, y = result["position"]
                    self.position_text.set(
                        f"SDK readback: X {x:.6f} mrad | Y {y:.6f} mrad"
                    )
                    self.values["x"].set(f"{x:.6f}")
                    self.values["y"].set(f"{y:.6f}")
                elif "raw" in result:
                    self.position_text.set(
                        "Raw SDK state received; see log. Angular conversion not yet active."
                    )
                if "point" in result:
                    self.progress_text.set(
                        f"Current: {result['point']['mean_current_a']:.6g} A; output off confirmed"
                    )
                self.status.set(
                    result.get("message", f"{payload['operation']} finished")
                )
            elif kind == "error":
                self.log(payload["traceback"])
                message = payload["message"] + "\n" + "\n".join(payload["notes"])
                self.status.set(f"{payload['type']}: {payload['message']}")
                if payload["operation"] == "map":
                    self.progress_text.set(
                        "Map stopped/failed. Retained pixels are partial; inspect CSV footer and log."
                    )
                if not self.closing:
                    self.messagebox.showerror("Operation stopped", message)
            elif kind == "idle":
                self.busy = False
                self.update_controls()
            elif kind == "stopped":
                if self.log_file is not None:
                    self.log_file.close()
                self.root.destroy()
                return
        if redraw:
            self.draw_map()
        if self.closing and not self.busy and not self.shutdown_sent:
            self.worker.submit("shutdown", {})
            self.shutdown_sent = True
            self.busy = True
        self.update_controls()
        self.root.after(100, self.poll)

    def close(self):
        if self.closing:
            return
        if not self.messagebox.askyesno(
            "Close",
            (
                "Cancel active work, wait for cleanup and disconnect?\n"
                "The mirror will remain at its last demand. Block the laser as appropriate."
            ),
        ):
            return
        self._save_preferences()
        self.closing = True
        self.worker.cancel.set()
        self.status.set(
            "Closing: waiting for I/O and cleanup. Do not unplug active hardware."
        )
        self.update_controls()

    def _restore_preferences(self):
        try:
            data = load_preferences(self.path)
            for key, value in data["values"].items():
                if key in self.values:
                    self.values[key].set(value)
            if "remote_sense" in data["flags"]:
                self.remote.set(data["flags"]["remote_sense"])
            if "serpentine" in data["flags"]:
                self.serpentine.set(data["flags"]["serpentine"])
            # Never restore connection, prepared state, calibration acknowledgement,
            # commissioning acknowledgement or a previous CSV filename.
            self.ack.set(False)
        except (OSError, ValueError, TypeError) as exc:
            self.log(f"Saved settings ignored: {exc}")

    def _cancel_preferences_save(self):
        if self._preferences_after is not None:
            self.root.after_cancel(self._preferences_after)
            self._preferences_after = None

    def _save_preferences(self):
        self._cancel_preferences_save()
        if self._preferences_loading:
            return
        try:
            save_preferences(
                self.path,
                {
                    key: variable.get()
                    for key, variable in self.values.items()
                    if key in FIELDS
                },
                {
                    "remote_sense": self.remote.get(),
                    "serpentine": self.serpentine.get(),
                },
            )
        except (OSError, ValueError, TypeError) as exc:
            self.log(f"Settings could not be saved: {exc}")

    def _preferences_changed(self, *_args):
        if self._preferences_loading or self.closing:
            return
        self._cancel_preferences_save()
        self._preferences_after = self.root.after(600, self._save_preferences)

    def _watch_preferences(self):
        variables = [variable for key, variable in self.values.items() if key in FIELDS]
        variables.extend((self.remote, self.serpentine))
        for variable in variables:
            trace = getattr(variable, "trace_add", None)
            if callable(trace):
                self._preferences_traces.append(
                    (variable, trace("write", self._preferences_changed))
                )


def launch(config_path, tab="mirror"):
    import tkinter as tk

    root = tk.Tk()
    app = Application(root, config_path)
    app.tabs.select(1 if tab == "mirror" else 2)
    try:
        root.mainloop()
    finally:
        # Join before returning/releasing the per-project instance lock.
        # No forced thread or SDK termination, even for blocked native calls.
        app.worker.request_shutdown()
        app.worker.wait_stopped()
        if app.log_file is not None and not app.log_file.closed:
            app.log_file.close()
