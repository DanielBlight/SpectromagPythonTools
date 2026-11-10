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
        return name

    def _io(self, channel):
        result = _IOSettings()
        self._ok(
            self._dll.PPC2_GetIOSettings(self._serial, channel, ct.byref(result)),
            "GetIOSettings",
        )
        return result

    def read(self, channel):
        self._ok(
            self._dll.PPC2_RequestSettings(self._serial, channel), "RequestSettings"
        )
        self._ok(self._dll.PPC2_RequestStatus(self._serial, channel), "RequestStatus")
        self._ok(
            self._dll.PPC2_RequestActualPosition(self._serial, channel),
            "RequestActualPosition",
        )
        time.sleep(self.response_wait_s)
        return RawState(
            channel,
            int(self._dll.PPC2_GetPosition(self._serial, channel)),
            int(self._dll.PPC2_GetStatusBits(self._serial, channel)),
            int(self._dll.PPC2_GetPositionControlMode(self._serial, channel)),
            int(self._io(channel).control_source),
            time.time(),
        )

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
        try:
            if self._serial is not None:
                self._dll.PPC_Close(self._serial)
        finally:
            self._serial = None
            if self._directory_handle is not None:
                self._directory_handle.close()
                self._directory_handle = None


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
                except BaseException as cleanup:  # noqa: BLE001
                    exc.add_note(f"Connection cleanup failed: {cleanup}")
                raise

    def raw_state(self):
        """Read SDK state for inspection; does not enable or command position."""
        with self._lock:
            self._require()
            try:
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
            or not state.status_bits & 0x100
            or not state.status_bits & 0x80000000
            or state.input_source != 0
        ):
            raise GimbalError(
                "Require enabled closed loop, feedback and software-only input"
            )

    def prepare_closed_loop(self):
        with self._lock:
            self._require()
            if self.x_calibration is None or self.y_calibration is None:
                raise GimbalError(
                    "Verify both channel count-to-mrad calibrations first"
                )
            try:
                for state in self.raw_state():
                    self._healthy(state, require_closed=False)
                    if not state.status_bits & 0x100:
                        raise GimbalError("Strain-gauge feedback is not connected")
                for channel in (self.x_channel, self.y_channel):
                    self._backend.prepare(channel)
                self._prepared = True
                self.position()
            except BaseException:
                self._faulted = True
                self._prepared = False
                raise

    def position(self):
        with self._lock:
            self._require()
            if self.x_calibration is None or self.y_calibration is None:
                raise GimbalError("Angular readback requires verified calibration")
            try:
                x, y = self.raw_state()
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
        """Set both demands sequentially; return measured SDK angles after settling.

        Stability is based on repeated CACHED SDK observations, not independent
        hardware timestamped samples. Validate freshness/USB-loss behaviour on
        your installed SDK before relying on it for unattended acquisition.
        """
        x, y = _finite(x_mrad, "x_mrad"), _finite(y_mrad, "y_mrad")
        if max(abs(x), abs(y)) > self.max_abs_mrad:
            raise ValueError("Target exceeds the configured mechanical-angle limit")
        tolerance = _finite(tolerance_mrad, "tolerance_mrad")
        stable = _finite(stable_s, "stable_s")
        timeout = _finite(timeout_s, "timeout_s")
        if (
            not 0 < tolerance <= 1
            or not 0.05 <= stable <= 10
            or not stable < timeout <= 120
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
                self.position()  # Verify both axes before either command.
                for channel, calibration, target in (
                    (self.x_channel, self.x_calibration, x),
                    (self.y_channel, self.y_calibration, y),
                ):
                    if cancel is not None and cancel.is_set():
                        raise MoveCancelled("Cancelled between axis commands")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Move deadline exceeded")
                    self._backend.set_position(channel, calibration.to_raw(target))
                in_tolerance_since = None
                while True:
                    if cancel is not None and cancel.is_set():
                        raise MoveCancelled("Cancelled while waiting")
                    measured = self.position()
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
                            return measured.x_mrad, measured.y_mrad
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
