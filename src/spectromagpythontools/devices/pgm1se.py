"""PGM1SE/PPC102 closed-loop angular positioning. No mapping or GUI logic.
Native Kinesis DLL loading is lazy. No hardware I/O on import/construction.
Angles are MECHANICAL mirror angles in mrad, not beam or sample coordinates.
"""

from __future__ import annotations

import ctypes as ct
import math
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from spectromagpythontools.routines.motion_profile import profile_move, timed


class GimbalError(RuntimeError):
    """Controller or acquisition state is unsuitable for positioning."""


class MoveCancelled(GimbalError):
    """No further commands will be sent; the last demand may remain active."""


def _finite(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not bool")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class AxisCalibration:
    """Verified SDK count mapping for -10 and +10 mrad, for ONE channel.
    No default scaling is supplied: generic SDK docs disagree about signed
    setpoints. Confirm the mapping against the matched controller's Kinesis
    display/API documentation. Do not determine it by blind full-range moves.
    """

    raw_minus_10: int
    raw_plus_10: int
    evidence: str

    def __post_init__(self):
        for value in (self.raw_minus_10, self.raw_plus_10):
            if type(value) is not int or not -32767 <= value <= 32767:
                raise ValueError("Calibration endpoints must be signed 16-bit counts")
        if self.raw_minus_10 == self.raw_plus_10:
            raise ValueError("Calibration endpoints must differ")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("Record how the angular conversion was verified")

    def to_raw(self, mrad):
        angle = _finite(mrad, "angle_mrad")
        if not -10 <= angle <= 10:
            raise ValueError("Mechanical angle must be within +/-10 mrad")
        return round(
            self.raw_minus_10
            + (angle + 10) / 20 * (self.raw_plus_10 - self.raw_minus_10)
        )

    def from_raw(self, raw):
        if type(raw) is not int or not -32767 <= raw <= 32767:
            raise GimbalError("Invalid raw position")
        return -10 + 20 * (raw - self.raw_minus_10) / (
            self.raw_plus_10 - self.raw_minus_10
        )

    @property
    def resolution_mrad(self):
        return 20 / abs(self.raw_plus_10 - self.raw_minus_10)


@dataclass(frozen=True)
class RawState:
    channel: int
    raw_position: int
    status_bits: int
    mode: int
    input_source: int
    feedback_source: int
    host_unix_s: float


@dataclass(frozen=True)
class Position:
    x_mrad: float
    y_mrad: float
    x_state: RawState
    y_state: RawState


class _IOSettings(ct.Structure):
    _pack_ = 1
    _fields_ = tuple(
        (name, ct.c_int16)
        for name in (
            "control_source",
            "monitor_signal",
            "monitor_bandwidth",
            "feedback_source",
            "brightness",
            "feedback_polarity",
        )
    )


class KinesisPPC102:
    """Thin cdecl bindings to the supplied PPC2_ header, not generic PBC_.
    Getters are Kinesis cached values. Request + delay is NOT a timestamped
    response acknowledgement. Native calls have SDK-defined blocking behaviour.
    """

    def __init__(
        self, directory=r"C:\Program Files\Thorlabs\Kinesis", response_wait_s=0.1
    ):
        self.directory = Path(directory)
        self.response_wait_s = _finite(response_wait_s, "response_wait_s")
        if not 0.02 <= self.response_wait_s <= 2:
            raise ValueError("response_wait_s must be in 0.02..2 seconds")
        self._dll = self._directory_handle = None
        self._serial = None

    def _bind(self, name, result, *args):
        function = getattr(self._dll, name)
        function.restype, function.argtypes = result, list(args)

    @staticmethod
    def _ok(code, operation):
        if code != 0:
            raise GimbalError(f"{operation} returned Kinesis error {code}")

    def open(self, serial):
        if self._serial is not None or self._dll is not None:
            raise GimbalError("Close the existing backend before opening")
        self._motion_settings = {}
        self._settings_checked_at = 0
        try:
            return self._open_device(serial)
        except BaseException as exc:
            try:
                self.close()
            except BaseException as cleanup:  # noqa: BLE001
                exc.add_note(f"Backend open cleanup failed: {cleanup}")
            raise

    def _wait_connected(self):
        # Reopened sessions can initially expose empty cached status.
        # Retry read-only startup observations, never movement/mode commands.
        deadline = time.monotonic() + 2.0
        states = ()
        for _ in range(10):
            states = self.read_channels((1, 2))
            for state in states:
                if state.status_bits & (0x800 | 0x1000 | 0x2000 | 0x40000000):
                    raise GimbalError("Controller fault during connection readiness")
            if len(states) == 2 and all(state.status_bits & 1 for state in states):
                return
            if time.monotonic() >= deadline:
                break
        bits = ", ".join(f"channel {s.channel}: 0x{s.status_bits:08x}" for s in states)
        raise GimbalError(
            f"Piezo actuator not connected after startup refresh ({bits})"
        )

    def _open_device(self, serial):
        if os.name != "nt":
            raise OSError("Real Kinesis acquisition requires Windows")
        dll_path = self.directory / "Thorlabs.MotionControl.Benchtop.PrecisionPiezo.dll"
        if not dll_path.is_file():
            raise FileNotFoundError(dll_path)
        self._directory_handle = os.add_dll_directory(str(self.directory))
        self._dll = ct.CDLL(str(dll_path))
        s, i, h = ct.c_char_p, ct.c_int, ct.c_int16
        self._bind("TLI_BuildDeviceList", h)
        self._bind("PPC_Open", h, s)
        self._bind("PPC_Close", None, s)
        self._bind(
            "PPC2_GetHardwareInfo",
            h,
            s,
            i,
            ct.c_char_p,
            ct.c_uint32,
            ct.POINTER(ct.c_uint16),
            ct.POINTER(ct.c_uint16),
            ct.c_char_p,
            ct.c_uint32,
            ct.POINTER(ct.c_uint32),
            ct.POINTER(ct.c_uint16),
            ct.POINTER(ct.c_uint16),
        )
        for name in (
            "RequestSettings",
            "RequestStatus",
            "RequestActualPosition",
            "EnableChannel",
        ):
            self._bind("PPC2_" + name, h, s, i)
        for name in ("GetPosition", "GetPositionControlMode"):
            self._bind("PPC2_" + name, h, s, i)
        self._bind("PPC2_GetStatusBits", ct.c_uint32, s, i)
        for name in ("SetPosition", "SetPositionControlMode"):
            self._bind("PPC2_" + name, h, s, i, h)
        for name in ("GetIOSettings", "SetIOSettings"):
            self._bind("PPC2_" + name, h, s, i, ct.POINTER(_IOSettings))
        self._ok(self._dll.TLI_BuildDeviceList(), "BuildDeviceList")
        encoded = serial.encode("ascii")
        self._ok(self._dll.PPC_Open(encoded), "PPC_Open")
        self._serial = encoded
        model, notes = ct.create_string_buffer(64), ct.create_string_buffer(128)
        kind, channels, firmware, hardware, modification = (
            ct.c_uint16(),
            ct.c_uint16(),
            ct.c_uint32(),
            ct.c_uint16(),
            ct.c_uint16(),
        )
        self._ok(
            self._dll.PPC2_GetHardwareInfo(
                encoded,
                1,
                model,
                len(model),
                ct.byref(kind),
                ct.byref(channels),
                notes,
                len(notes),
                ct.byref(firmware),
                ct.byref(hardware),
                ct.byref(modification),
            ),
            "GetHardwareInfo",
        )
        name = model.value.decode("ascii", errors="replace").strip()
        if name != "PPC102":
            raise GimbalError(f"Expected PPC102, got {name!r}")
        for channel in (1, 2):
            self._ok(
                self._dll.PPC2_RequestSettings(encoded, channel), "RequestSettings"
            )
        time.sleep(self.response_wait_s)
        self._wait_connected()
        return name

    def _io(self, channel):
        result = _IOSettings()
        self._ok(
            self._dll.PPC2_GetIOSettings(self._serial, channel, ct.byref(result)),
            "GetIOSettings",
        )
        return result

    def read_channels(self, channels):
        """Full configuration/status inspection; also refresh the fast-path cache."""
        return self._read_channels(channels, refresh_settings=True)

    def read_motion_channels(self, channels):
        """Live position/status checks; share the full-settings clock with mount.
        Cached SDK observations, not certified response acknowledgements.
        """
        channels = tuple(channels)
        cache = getattr(self, "_motion_settings", {})
        refresh = any(channel not in cache for channel in channels)
        from spectromagpythontools.routines.scan_settings import active

        if active(self):
            if refresh:
                raise GimbalError(
                    "Scan settings cache lost; reconnect and prepare again"
                )
        else:
            refresh = (
                refresh
                or time.monotonic() - getattr(self, "_settings_checked_at", 0)
                >= self._coordinated_settings_interval()
            )
        mode = os.environ.get("SPECTROMAG_FEEDBACK_REQUEST", "legacy")
        if mode not in ("legacy", "combined", "combined_refresh"):
            raise ValueError(
                "SPECTROMAG_FEEDBACK_REQUEST must be legacy or combined (or combined_refresh)"
            )
        combined = (
            mode in ("combined", "combined_refresh")
            and os.environ.get("SPECTROMAG_MIRROR_POLICY") == "one_check"
            and (not refresh or mode == "combined_refresh")
        )
        return self._read_channels(
            channels, refresh_settings=refresh, combined_status=combined
        )

    def _coordinated_settings_interval(self):
        import os

        interval = _finite(
            os.environ.get(
                "SPECTROMAG_MIRROR_FULL_CHECK_S",
                "2"
                if os.environ.get("SPECTROMAG_MIRROR_POLICY") == "one_check"
                else "1",
            ),
            "full check interval",
        )
        if not 0.5 <= interval <= 5:
            raise ValueError("Full check interval must be in 0.5..5 seconds")
        return interval

    def _read_channels(self, channels, *, refresh_settings, combined_status=False):
        channels = tuple(channels)
        if (
            not channels
            or len(set(channels)) != len(channels)
            or any(
                type(channel) is not int or channel not in (1, 2)
                for channel in channels
            )
        ):
            raise ValueError(
                "channels must contain distinct PPC102 channels 1 and/or 2"
            )
        if not hasattr(self, "_motion_settings"):
            self._motion_settings = {}
        request_calls = []

        def request(function, name, channel):
            started = time.perf_counter()
            try:
                self._ok(function(self._serial, channel), name)
            finally:
                request_calls.append(
                    {
                        "api": name,
                        "channel": channel,
                        "elapsed_s": time.perf_counter() - started,
                    }
                )

        request_started = time.perf_counter()
        for channel in channels:
            if refresh_settings:
                request(self._dll.PPC2_RequestSettings, "RequestSettings", channel)
            request(self._dll.PPC2_RequestStatus, "RequestStatus", channel)
            if not combined_status:
                request(
                    self._dll.PPC2_RequestActualPosition,
                    "RequestActualPosition",
                    channel,
                )
        requests_s = time.perf_counter() - request_started
        wait_started = time.perf_counter()
        time.sleep(self.response_wait_s)
        wait_s = time.perf_counter() - wait_started
        getters_started = time.perf_counter()
        states = []
        for channel in channels:
            if refresh_settings:
                settings = self._io(channel)
                self._motion_settings[channel] = (
                    int(settings.control_source),
                    int(settings.feedback_source),
                )
            source, feedback = self._motion_settings[channel]
            states.append(
                RawState(
                    channel,
                    int(self._dll.PPC2_GetPosition(self._serial, channel)),
                    int(self._dll.PPC2_GetStatusBits(self._serial, channel)),
                    int(self._dll.PPC2_GetPositionControlMode(self._serial, channel)),
                    source,
                    feedback,
                    time.time(),
                )
            )
        if refresh_settings:
            self._settings_checked_at = time.monotonic()
        self._last_feedback_read = {
            "mode": "combined" if combined_status else "legacy",
            "refresh_settings": refresh_settings,
            "channels": list(channels),
            "requests_s": requests_s,
            "request_calls": request_calls,
            "response_wait_s": wait_s,
            "getters_s": time.perf_counter() - getters_started,
            "actual_position_requests": 0 if combined_status else len(channels),
            "status_requests": len(channels),
            "freshness": "Cached SDK values; no correlated response acknowledgement",
        }
        return tuple(states)

    def read(self, channel):
        """Read one channel using the same batched request path."""
        return self.read_channels((channel,))[0]

    def prepare(self, channel):
        settings = self._io(channel)
        settings.control_source = 0  # Software only; preserve all other fields.
        self._ok(
            self._dll.PPC2_SetIOSettings(self._serial, channel, ct.byref(settings)),
            "SetIOSettings",
        )
        self._ok(
            self._dll.PPC2_SetPositionControlMode(self._serial, channel, 2),
            "SetPositionControlMode",
        )
        self._ok(self._dll.PPC2_EnableChannel(self._serial, channel), "EnableChannel")

    def set_position(self, channel, raw):
        self._ok(self._dll.PPC2_SetPosition(self._serial, channel, raw), "SetPosition")

    def close(self):
        serial, dll = self._serial, self._dll
        handle = self._directory_handle
        self._serial = self._dll = self._directory_handle = None
        self._motion_settings = {}
        self._settings_checked_at = 0
        try:
            if serial is not None and dll is not None:
                dll.PPC_Close(serial)
        finally:
            if handle is not None:
                handle.close()


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
        from spectromagpythontools.routines.scan_position_cache import invalidate

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
        from spectromagpythontools.routines.scan_position_cache import invalidate

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
        from spectromagpythontools.routines.scan_position_cache import invalidate

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

        from spectromagpythontools.routines.scan_position_cache import (
            accept_position as _scan_accept,
        )
        from spectromagpythontools.routines.scan_position_cache import (
            initial_position as _scan_initial,
        )
        from spectromagpythontools.routines.scan_position_cache import (
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
                from spectromagpythontools.routines.scan_settings import active

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
                from spectromagpythontools.routines.first_check_profile import (
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
