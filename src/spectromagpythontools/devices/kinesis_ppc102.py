"""Native Kinesis PPC102 transport; DLLs load only when explicitly opened."""

from __future__ import annotations

import ctypes as ct
import os
import time
from pathlib import Path

from spectromagpythontools.devices.gimbal_types import GimbalError, RawState, _finite


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
        from spectromagpythontools.devices.scan_settings import active

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
