"""One hardware owner and one serial worker. No Tk or other GUI imports."""

from __future__ import annotations

import queue
import threading
import traceback
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

from spectromagpythontools.configuration import (
    axis_values,
    build_mount,
    build_smu,
    integer,
    measurement,
    mount_metadata,
    numeric,
)
from spectromagpythontools.devices.keithley2600 import AcquisitionCancelled
from spectromagpythontools.devices.pgm1se import MoveCancelled
from spectromagpythontools.routines.gimbal_positioning import gimbal_mover
from spectromagpythontools.routines.photocurrent_map import read_point, scan_map


class Session:
    """Called ONLY on the worker thread. Device factories are injectable for tests."""

    def __init__(self, emit, *, mount_factory=build_mount, smu_factory=build_smu):
        self.emit = emit
        self.mount_factory, self.smu_factory = mount_factory, smu_factory
        self.mount = None
        self.prepared = False

    def state(self):
        return {"connected": self.mount is not None, "prepared": self.prepared}

    def connect(self, config, cancel):
        if self.mount is not None:
            raise RuntimeError("Disconnect the mirror before reconnecting")
        mount = self.mount_factory(config["pgm1se"])
        try:
            mount.connect()
            self.mount = mount
            self.prepared = False
            return self.inspect({}, cancel)
        except BaseException as exc:
            self.mount = None
            self.prepared = False
            try:
                mount.close()
            except BaseException as cleanup:  # noqa: BLE001
                exc.add_note(f"Candidate mirror cleanup failed: {cleanup}")
            raise

    def inspect(self, config, cancel):
        if self.mount is None:
            raise RuntimeError("Connect the mirror first")
        raw = [asdict(state) for state in self.mount.raw_state()]
        result = {"raw": raw}
        if self.prepared:
            position = self.mount.position()
            result["position"] = (position.x_mrad, position.y_mrad)
        return result

    def prepare(self, config, cancel):
        if self.mount is None:
            raise RuntimeError("Connect the mirror first")
        if cancel.is_set():
            raise MoveCancelled("Cancelled before preparing")
        self.mount.prepare_closed_loop()
        self.prepared = True
        return self.inspect({}, cancel)

    def move(self, config, cancel):
        if not self.prepared:
            raise RuntimeError("Prepare closed loop first")
        x = numeric(config["x"], "X (mrad)", -10, 10)
        y = numeric(config["y"], "Y (mrad)", -10, 10)
        dx = numeric(config.get("dx", 0), "X jog (mrad)", -10, 10)
        dy = numeric(config.get("dy", 0), "Y jog (mrad)", -10, 10)
        if dx or dy:
            # Jog from current readback, not a stale GUI target.
            position = self.mount.position()
            x, y = position.x_mrad + dx, position.y_mrad + dy
        result = self.mount.move_and_wait(x, y, cancel=cancel, **self.motion(config))
        return {"position": result}

    @staticmethod
    def motion(config):
        tolerance = numeric(
            config.get("mirror_tolerance_mrad", 0.01), "tolerance", 1e-6, 1
        )
        stable = numeric(
            config.get("mirror_stable_s", 0.2), "stable interval", 0.05, 10
        )
        timeout = numeric(config.get("mirror_timeout_s", 10), "move timeout", 0.1, 120)
        if timeout <= stable:
            raise ValueError("Move timeout must exceed the stable interval")
        return {"tolerance_mrad": tolerance, "stable_s": stable, "timeout_s": timeout}

    def disconnect(self, config, cancel):
        mount, self.mount = self.mount, None
        self.prepared = False
        if mount is not None:
            mount.close()
        return {
            "message": "Mirror disconnected; no parking or output-disable command sent"
        }

    def identify(self, config, cancel):
        # No sample bias/current setting is applied. Driver connect forces high-Z off.
        with self.smu_factory(
            config["keithley"], max_abs_bias_v=0, max_current_a=1e-8
        ) as smu:
            return {
                "identity": smu.identity,
                "message": "Keithley identified; output off confirmed",
            }

    def resources(self, config, cancel):
        import pyvisa

        with pyvisa.ResourceManager(
            config["keithley"].get("visa_library", "")
        ) as manager:
            return {"resources": manager.list_resources("USB?*::INSTR")}

    def read_current(self, config, cancel):
        max_v, max_i, settings = measurement(config)
        samples = integer(config["samples"], "samples", 1, 1000)
        settle = numeric(config["settle_s"], "settle", 0, 60)
        with self.smu_factory(
            config["keithley"], max_abs_bias_v=max_v, max_current_a=max_i
        ) as smu:
            smu.configure(settings)
            if cancel.is_set():
                raise AcquisitionCancelled("Cancelled before output enable")
            with smu.output_enabled():
                point = read_point(smu, samples=samples, settle_s=settle, cancel=cancel)
            return {
                "point": asdict(point),
                "identity": smu.identity,
                "message": "Current acquired; output off confirmed",
            }

    def map(self, config, cancel):
        from spectromagpythontools.devices.scan_position_cache import (
            scan_position_reuse,
        )

        if not self.prepared:
            raise RuntimeError("Connect and prepare the mirror first")
        if (
            getattr(self, "requires_ack", True)
            and config.get("commissioning_ack") is not True
        ):
            raise ValueError(
                "Acknowledge SDK freshness and laser/beam-path checks before mapping"
            )
        xs = axis_values(config["x_start"], config["x_end"], config["nx"])
        ys = axis_values(config["y_start"], config["y_end"], config["ny"])
        if max(abs(v) for v in (*xs, *ys)) > self.mount.max_abs_mrad:
            raise ValueError("Grid exceeds the connected mirror's software limit")
        motion = self.motion(config)
        samples = integer(config["samples"], "samples", 1, 1000)
        settle = numeric(config["settle_s"], "pixel settle", 0, 60)
        max_v, max_i, settings = measurement(config)
        target = Path(config["output_csv"]).expanduser().resolve()
        if target.exists():
            raise FileExistsError(f"Will not overwrite {target}")
        if not target.parent.is_dir():
            raise ValueError("Choose an existing output directory")
        if target.suffix.lower() != ".csv":
            raise ValueError("Choose a .csv output file")
        if cancel.is_set():
            raise AcquisitionCancelled("Cancelled before acquisition")
        metadata = {
            "application": config.get(
                "application", "SpectromagPythonTools commissioning GUI v1"
            ),
            "mirror": mount_metadata(self.mount),
            "motion": motion,
            **(
                {"commissioning_ack": True}
                if getattr(self, "requires_ack", True)
                else {}
            ),
            "measurement_label": str(config.get("measurement_label", "unclassified")),
            "dark_subtracted": False,
            "scan_position_reuse": "scan-scoped accepted SDK coordinate; post-move verification retained in one_check mode",
        }
        from spectromagpythontools.diagnostics.motion_profile import capture_motion

        profile_path = target.with_suffix(".motion.txt")
        with (
            capture_motion(self.mount, profile_path),
            self.smu_factory(
                config["keithley"], max_abs_bias_v=max_v, max_current_a=max_i
            ) as smu,
        ):
            smu.configure(settings)
            with scan_position_reuse(self.mount):
                count = scan_map(
                    smu,
                    xs=xs,
                    ys=ys,
                    move_and_wait=gimbal_mover(self.mount, cancel=cancel, **motion),
                    output_csv=target,
                    samples=samples,
                    settle_s=settle,
                    serpentine=config.get("serpentine", True),
                    position_unit="mrad",
                    cancel=cancel,
                    acquisition_metadata=metadata,
                    on_pixel=lambda pixel: self.emit("pixel", pixel),
                )
        return {
            "pixels": count,
            "motion_profile": str(profile_path),
            "output_csv": str(target),
            "message": "Map complete; Keithley output off confirmed; mirror left at last position",
        }


class HardwareWorker:
    """At most ONE outstanding command; all instrument calls stay on one thread.

    Cancellation is a shared Event, not a queued command. No forced thread kill.
    A stuck vendor call cannot be interrupted; the UI stays open until it returns.
    """

    def __init__(self, session_factory=Session):
        self.events = queue.SimpleQueue()
        self.cancel = threading.Event()
        self._commands = queue.Queue(maxsize=1)
        self._guard = threading.Lock()
        self._busy = False
        self._stopped = False
        self._shutdown_requested = threading.Event()
        self._session_factory = session_factory
        self._thread = threading.Thread(
            target=self._run, name="spectromag-hardware", daemon=False
        )
        self._thread.start()

    @property
    def busy(self):
        with self._guard:
            return self._busy

    def submit(self, operation, config):
        allowed = {
            "connect",
            "inspect",
            "prepare",
            "move",
            "disconnect",
            "identify",
            "resources",
            "read_current",
            "map",
            "shutdown",
        }
        if operation not in allowed:
            raise ValueError(f"Unknown operation {operation}")
        snapshot = deepcopy(config)
        with self._guard:
            if self._busy or self._stopped or self._shutdown_requested.is_set():
                raise RuntimeError("Hardware worker is busy or stopped")
            self._busy = True
            self.cancel.clear()
            self._commands.put_nowait((operation, snapshot))

    def request_shutdown(self):
        """Cooperative close, including when mainloop exits unexpectedly."""
        self.cancel.set()
        self._shutdown_requested.set()

    def wait_stopped(self):
        if threading.current_thread() is self._thread:
            raise RuntimeError("Worker cannot join itself")
        self._thread.join()

    def _emit(self, kind, value):
        self.events.put((kind, value))

    def _run(self):
        session = self._session_factory(self._emit)
        while True:
            queued = False
            try:
                operation, config = self._commands.get(timeout=0.1)
                queued = True
            except queue.Empty:
                if not self._shutdown_requested.is_set():
                    continue
                operation, config = "shutdown", {}
            shutdown = operation == "shutdown"
            try:
                if shutdown:
                    result = session.disconnect({}, self.cancel)
                else:
                    result = getattr(session, operation)(config, self.cancel)
                self._emit("result", {"operation": operation, "value": result})
            except BaseException as exc:  # noqa: BLE001
                if operation in {"connect", "inspect", "prepare", "move", "map"}:
                    session.prepared = False
                self._emit(
                    "error",
                    {
                        "operation": operation,
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "notes": list(getattr(exc, "__notes__", [])),
                        "traceback": traceback.format_exc(),
                    },
                )
            finally:
                self._emit("state", session.state())
                with self._guard:
                    self._busy = False
                    if shutdown:
                        self._stopped = True
                self._emit("idle", {"operation": operation})
                if queued:
                    self._commands.task_done()
            if shutdown:
                self._emit("stopped", {})
                return


# Descriptive aliases; preserve existing imports and object identity.
InstrumentSession = Session
InstrumentWorker = HardwareWorker
