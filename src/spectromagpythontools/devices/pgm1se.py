"""PGM1SE mount control; public device imports remain backward compatible."""

from __future__ import annotations

import re
import threading
import time

from spectromagpythontools.devices.gimbal_types import (
    AxisCalibration,
)
from spectromagpythontools.devices.gimbal_types import (
    GimbalError,
)
from spectromagpythontools.devices.gimbal_types import (
    MoveCancelled,
)
from spectromagpythontools.devices.gimbal_types import (
    Position,
)
from spectromagpythontools.devices.gimbal_types import (
    RawState,
)
from spectromagpythontools.devices.gimbal_types import (
    _finite,
)
from spectromagpythontools.devices.kinesis_ppc102 import (
    KinesisPPC102,
)
from spectromagpythontools.devices.kinesis_ppc102 import (
    _IOSettings,
)
from spectromagpythontools.diagnostics.motion_profile import profile_move, timed


__all__ = [
    "AxisCalibration",
    "GimbalError",
    "MoveCancelled",
    "Position",
    "RawState",
    "KinesisPPC102",
    "PGM1SE",
    "_IOSettings",
]


class PGM1SE:
    """Two-axis closed-loop mount. No scan grid, CSV or photocurrent logic.
    connect() requests state only. prepare_closed_loop() explicitly changes
    input source, mode and enable state, which MAY move the beam.
    close() disconnects WITHOUT parking, disabling or changing the demand.
    On timeout/fault/cancel no automatic rollback or centring is attempted.
    """

    def __init__(
        self,
        serial,
        *,
        x_channel=1,
        y_channel=2,
        x_calibration=None,
        y_calibration=None,
        max_abs_mrad=10,
        backend=None,
    ):
        if not isinstance(serial, str) or not re.fullmatch(r"95[0-9]{6}", serial):
            raise ValueError("Supply the eight-digit PPC102 serial starting 95")
        if (
            type(x_channel) is not int
            or type(y_channel) is not int
            or {x_channel, y_channel} != {1, 2}
        ):
            raise ValueError("Map X/Y to distinct channels 1 and 2")
        self.max_abs_mrad = _finite(max_abs_mrad, "max_abs_mrad")
        if not 0 < self.max_abs_mrad <= 10:
            raise ValueError("Software angular limit must be >0 and <=10 mrad")
        for calibration in (x_calibration, y_calibration):
            if calibration is not None and not isinstance(calibration, AxisCalibration):
                raise TypeError("Calibration must be AxisCalibration or None")
        self.serial = serial
        self.x_channel, self.y_channel = x_channel, y_channel
        self.x_calibration, self.y_calibration = x_calibration, y_calibration
        self._backend = backend if backend is not None else KinesisPPC102()
        self._lock = threading.RLock()
        self._connected = self._prepared = self._faulted = self._used = False

    def _require(self):
        if not self._connected or self._faulted:
            raise GimbalError("A healthy connected instance is required")

    def connect(self):
        from spectromagpythontools.devices.scan_position_cache import invalidate

        invalidate(self)
        self._fast_checked_at = None
        with self._lock:
            if self._used:
                raise GimbalError("Instances are single-use; construct a new one")
            self._used = True
            try:
                identity = self._backend.open(self.serial)
                if identity != "PPC102":
                    raise GimbalError(f"Expected PPC102, got {identity!r}")
                self._connected = True
                return self
            except BaseException as exc:
                try:
                    self._backend.close()
                except BaseException as cleanup:  # noqa: BLE001 - cleanup before outer re-raise
                    exc.add_note(f"Connection cleanup failed: {cleanup}")
                raise

    def raw_state(self):
        """Read both axes using one shared SDK response interval."""
        with self._lock:
            self._require()
            try:
                read_channels = getattr(
                    self._backend,
                    "read_channels",
                    None,
                )
                if callable(read_channels):
                    return read_channels(
                        (
                            self.x_channel,
                            self.y_channel,
                        )
                    )
                # Retain compatibility with synthetic or custom backends.
                return (
                    self._backend.read(self.x_channel),
                    self._backend.read(self.y_channel),
                )
            except BaseException:
                self._faulted = True
                raise

    @staticmethod
    def _healthy(state, require_closed=True):
        if state.status_bits & (0x800 | 0x1000 | 0x2000 | 0x40000000):
            raise GimbalError(
                "Controller reports overtemperature, out-of-range position or error"
            )
        if not state.status_bits & 1:
            raise GimbalError("Piezo actuator not connected")
        if require_closed and (
            state.mode != 2
            or not state.status_bits & 0x400
            or state.feedback_source not in (1, 3)
            or not state.status_bits & 0x80000000
            or state.input_source != 0
        ):
            raise GimbalError(
                "Require enabled closed loop, feedback and software-only input"
            )

    def prepare_closed_loop(self):
        from spectromagpythontools.devices.scan_position_cache import invalidate

        invalidate(self)
        self._fast_checked_at = None
        with self._lock:
            self._require()
            if self.x_calibration is None or self.y_calibration is None:
                raise GimbalError(
                    "Verify both channel count-to-mrad calibrations first"
                )
            try:
                for state in self.raw_state():
                    self._healthy(state, require_closed=False)
                    if state.feedback_source not in (1, 3):
                        raise GimbalError("PGM1SE/M feedback source is not active")
                for channel in (self.x_channel, self.y_channel):
                    self._backend.prepare(channel)
                self._prepared = True
                self.position()
            except BaseException:
                self._faulted = True
                self._prepared = False
                raise

    def position(self):
        """Full settings/status/position inspection."""
        return self._checked_position(fast=False)

    def _motion_position(self):
        return self._checked_position(fast=True)

    def _checked_position(self, *, fast):
        with self._lock:
            self._require()
            if self.x_calibration is None or self.y_calibration is None:
                raise GimbalError("Angular readback requires verified calibration")
            try:
                reader = (
                    getattr(self._backend, "read_motion_channels", None)
                    if fast
                    else None
                )
                if callable(reader):
                    x, y = reader((self.x_channel, self.y_channel))
                else:
                    x, y = self.raw_state()
                record = getattr(self, "_active_motion_profile", None)
                feedback = getattr(self._backend, "_last_feedback_read", None)
                if record is not None and feedback is not None:
                    reads = record.setdefault("feedback_reads", [])
                    if len(reads) < 128:
                        reads.append(dict(feedback))
                self._healthy(x)
                self._healthy(y)
                angles = (
                    self.x_calibration.from_raw(x.raw_position),
                    self.y_calibration.from_raw(y.raw_position),
                )
                if any(abs(angle) > 10.01 for angle in angles):
                    raise GimbalError(
                        "Readback outside physical range; check calibration"
                    )
                return Position(*angles, x, y)
            except BaseException:
                self._faulted = True
                raise

    def _verified_move_and_wait(
        self,
        x_mrad,
        y_mrad,
        *,
        tolerance_mrad=0.01,
        stable_s=0.2,
        timeout_s=10,
        cancel=None,
    ):
        """Set both demands sequentially; return measured SDK angles after settling.
        Stability is based on repeated CACHED SDK observations, not independent
        hardware timestamped samples. Validate freshness/USB-loss behaviour on
        your installed SDK before relying on it for unattended acquisition.
        """
        x, y = (_finite(x_mrad, "x_mrad"), _finite(y_mrad, "y_mrad"))
        if max(abs(x), abs(y)) > self.max_abs_mrad:
            raise ValueError("Target exceeds the configured mechanical-angle limit")
        tolerance = _finite(tolerance_mrad, "tolerance_mrad")
        stable = _finite(stable_s, "stable_s")
        timeout = _finite(timeout_s, "timeout_s")
        if (
            not 0 < tolerance <= 1
            or not 0.05 <= stable <= 10
            or (not stable < timeout <= 120)
        ):
            raise ValueError("Invalid tolerance, stable interval or timeout")
        with self._lock:
            self._require()
            if not self._prepared:
                raise GimbalError("Explicitly prepare closed-loop operation first")
            if tolerance < max(
                self.x_calibration.resolution_mrad, self.y_calibration.resolution_mrad
            ):
                raise ValueError("Tolerance is below one calibrated SDK count")
            if cancel is not None and cancel.is_set():
                raise MoveCancelled("Cancelled before any move command")
            deadline = time.monotonic() + timeout
            try:
                if not getattr(self, "_fast_continue_settling", False):
                    self.position()
                if not getattr(self, "_fast_continue_settling", False):
                    for channel, calibration, target in (
                        (self.x_channel, self.x_calibration, x),
                        (self.y_channel, self.y_calibration, y),
                    ):
                        if cancel is not None and cancel.is_set():
                            raise MoveCancelled("Cancelled between axis commands")
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Move deadline exceeded")
                        timed(
                            self,
                            "command",
                            self._backend.set_position,
                            channel,
                            calibration.to_raw(target),
                        )
                in_tolerance_since = None
                while True:
                    if cancel is not None and cancel.is_set():
                        raise MoveCancelled("Cancelled while waiting")
                    measured = timed(self, "post_read", self._motion_position)
                    now = time.monotonic()
                    if now >= deadline:
                        raise TimeoutError("Gimbal did not settle before timeout")
                    if (
                        abs(measured.x_mrad - x) <= tolerance
                        and abs(measured.y_mrad - y) <= tolerance
                    ):
                        if in_tolerance_since is None:
                            in_tolerance_since = now
                        elif now - in_tolerance_since >= stable:
                            return (measured.x_mrad, measured.y_mrad)
                    else:
                        in_tolerance_since = None
                    if cancel is None:
                        time.sleep(0.02)
                    elif cancel.wait(0.02):
                        raise MoveCancelled("Cancelled while waiting")
            except BaseException:
                self._faulted = True
                self._prepared = False
                raise

    def close(self):
        from spectromagpythontools.devices.scan_position_cache import invalidate

        invalidate(self)
        self._fast_checked_at = None
        with self._lock:
            try:
                if self._used:
                    self._backend.close()
            finally:
                self._connected = self._prepared = False

    def __enter__(self):
        return self.connect()

    def __exit__(self, kind, exc, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if exc is None:
                raise
            exc.add_note(f"Gimbal disconnect failed: {cleanup}")
        return False

    @profile_move
    def move_and_wait(
        self,
        x_mrad,
        y_mrad,
        *,
        tolerance_mrad=0.01,
        stable_s=0.2,
        timeout_s=10,
        cancel=None,
    ):
        """Explicit opt-in fast path; default remains verified continuous settling.
        Fast mode checks cached position/status before and after each command but
        does not demonstrate continuous stability. Full settings checks are periodic.
        """
        import os

        if os.environ.get("SPECTROMAG_MIRROR_POLICY", "verified") != "one_check":
            return self._verified_move_and_wait(
                x_mrad,
                y_mrad,
                tolerance_mrad=tolerance_mrad,
                stable_s=stable_s,
                timeout_s=timeout_s,
                cancel=cancel,
            )
        return self._one_check_move(
            x_mrad,
            y_mrad,
            tolerance_mrad=tolerance_mrad,
            stable_s=stable_s,
            timeout_s=timeout_s,
            cancel=cancel,
        )

    @profile_move
    def _one_check_move(
        self, x_mrad, y_mrad, *, tolerance_mrad, stable_s, timeout_s, cancel
    ):
        import os

        from spectromagpythontools.devices.scan_position_cache import (
            accept_position as _scan_accept,
        )
        from spectromagpythontools.devices.scan_position_cache import (
            initial_position as _scan_initial,
        )
        from spectromagpythontools.devices.scan_position_cache import (
            invalidate as _scan_invalidate,
        )

        x, y = (_finite(x_mrad, "x_mrad"), _finite(y_mrad, "y_mrad"))
        tolerance = _finite(tolerance_mrad, "tolerance_mrad")
        stable, timeout = (
            _finite(stable_s, "stable_s"),
            _finite(timeout_s, "timeout_s"),
        )
        delay = _finite(
            os.environ.get("SPECTROMAG_MIRROR_DELAY_S", "0.05"), "fast delay"
        )
        interval = _finite(
            os.environ.get("SPECTROMAG_MIRROR_FULL_CHECK_S", "2"), "full check interval"
        )
        if (
            max(abs(x), abs(y)) > self.max_abs_mrad
            or not 0 < tolerance <= 1
            or (not 0.05 <= stable <= 10)
            or (not stable < timeout <= 120)
            or (not 0.05 <= delay <= 0.5)
            or (not 0.5 <= interval <= 5)
        ):
            raise ValueError("Invalid fast mirror target, tolerance or timing")
        with self._lock:
            self._require()
            if not self._prepared:
                raise GimbalError("Explicitly prepare closed-loop operation first")
            if self.x_calibration is None or self.y_calibration is None:
                raise GimbalError("Verified calibrations required")
            if tolerance < max(
                self.x_calibration.resolution_mrad, self.y_calibration.resolution_mrad
            ):
                raise ValueError("Tolerance is below one calibrated SDK count")
            deadline = time.monotonic() + timeout

            def checkpoint():
                if cancel is not None and cancel.is_set():
                    raise MoveCancelled("Fast move cancelled")
                if time.monotonic() >= deadline:
                    raise TimeoutError("Fast move host deadline exceeded")

            try:
                checkpoint()
                checked = getattr(self, "_fast_checked_at", None)
                backend_checked = getattr(self._backend, "_settings_checked_at", None)
                if checked is not None and backend_checked is not None:
                    checked = max(checked, backend_checked)
                from spectromagpythontools.devices.scan_settings import active

                if not active(self._backend) and (
                    checked is None or time.monotonic() - checked >= interval
                ):
                    initial = timed(self, "initial_full_read", self.position)
                    self._fast_checked_at = time.monotonic()
                else:
                    initial = timed(self, "initial_position", _scan_initial, self)
                checkpoint()
                for channel, calibration, actual, target in (
                    (self.x_channel, self.x_calibration, initial.x_mrad, x),
                    (self.y_channel, self.y_calibration, initial.y_mrad, y),
                ):
                    checkpoint()
                    if abs(actual - target) > tolerance:
                        timed(
                            self,
                            "command",
                            self._backend.set_position,
                            channel,
                            calibration.to_raw(target),
                        )
                if cancel is None:
                    timed(self, "delay", time.sleep, delay)
                elif timed(self, "delay", cancel.wait, delay):
                    raise MoveCancelled("Cancelled during fast settling delay")
                checkpoint()
                measured = timed(self, "post_read", self._motion_position)
                checkpoint()
                error = timed(
                    self,
                    "tolerance_check",
                    lambda: max(abs(measured.x_mrad - x), abs(measured.y_mrad - y)),
                )
                from spectromagpythontools.diagnostics.first_check_profile import (
                    record_first_check,
                )

                record_first_check(
                    self, initial, measured, x, y, tolerance, delay, error
                )
                if error <= tolerance:
                    self._last_fast_move = {
                        "policy": "one_check",
                        "fallback": False,
                        "error_mrad": error,
                    }
                    return _scan_accept(self, measured)
                remaining = deadline - time.monotonic()
                if remaining <= stable:
                    raise TimeoutError("No time left for verified settling fallback")
                self._active_motion_profile["fallback"] = True
                self._fast_continue_settling = True
                try:
                    _scan_invalidate(self)
                    result = timed(
                        self,
                        "fallback",
                        self._verified_move_and_wait,
                        x,
                        y,
                        tolerance_mrad=tolerance,
                        stable_s=stable,
                        timeout_s=remaining,
                        cancel=cancel,
                    )
                finally:
                    self._fast_continue_settling = False
                self._fast_checked_at = time.monotonic()
                self._last_fast_move = {
                    "policy": "one_check",
                    "fallback": True,
                    "first_error_mrad": error,
                }
                return result
            except BaseException:
                _scan_invalidate(self)
                self._fast_checked_at = None
                self._faulted = True
                self._prepared = False
                raise
