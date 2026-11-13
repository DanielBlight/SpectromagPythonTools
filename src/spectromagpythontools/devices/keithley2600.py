"""Qt-free instrument communication for Keithley 2600B.

Exclusive instrument ownership is required. No hardware I/O occurs on import.
Commands follow 2600BS-901-01 Rev. B, sections 7-198 through 7-232.
"""

from __future__ import annotations

import math
import re
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass


class InstrumentError(RuntimeError):
    """Acquisition/configuration failed; output-off may be unconfirmed."""


class AcquisitionCancelled(InstrumentError):
    pass


def finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number, not bool")  # noqa: TRY004
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def number(text):
    value = finite(text, "instrument response")
    if abs(value) >= 9e37:
        raise InstrumentError("Instrument returned an overflow reading")
    return value


@dataclass(frozen=True)
class Settings:
    bias_v: float
    current_limit_a: float
    nplc: float = 1.0
    current_range_a: float | None = None
    remote_sense: bool = False

    def __post_init__(self):
        for name in ("bias_v", "current_limit_a", "nplc"):
            object.__setattr__(self, name, finite(getattr(self, name), name))
        if self.current_limit_a <= 0 or not 0.001 <= self.nplc <= 25:
            raise ValueError("Positive current limit and 0.001 <= NPLC <= 25 required")
        if self.current_range_a is not None:
            value = finite(self.current_range_a, "current_range_a")
            if value <= 0:
                raise ValueError("Current range must be positive")
            object.__setattr__(self, "current_range_a", value)
        if type(self.remote_sense) is not bool:
            raise ValueError("remote_sense must be bool")


@dataclass(frozen=True)
class Reading:
    current_a: float
    voltage_v: float
    range_a: float
    host_started_unix_s: float
    host_finished_unix_s: float
    timestamp_basis: str = "host_single_read_bounds"


class KeithleyPhotocurrent:
    """One selected channel, fixed bias, explicit output enable.

    max_abs_bias_v and max_current_a are USER-CHOSEN DUT limits, not
    instrument ratings. This initial wrapper additionally caps them at
    20 V and 0.1 A. No ramps, fly scans, or trigger programming are provided.
    An injected session is borrowed, but its selected channel is still owned
    and switched off on close. Never share the instrument with other clients.
    """

    MODELS = frozenset(
        {
            "2601B",
            "2602B",
            "2604B",
            "2611B",
            "2612B",
            "2614B",
            "2634B",
            "2635B",
            "2636B",
        }
    )
    SINGLE = frozenset({"2601B", "2611B", "2635B"})

    def __init__(
        self,
        resource: str,
        *,
        expected_model: str,
        max_abs_bias_v: float,
        max_current_a: float,
        channel: str = "a",
        timeout_ms: int = 5000,
        visa_library: str = "",
        session=None,
    ):
        if expected_model not in self.MODELS:
            raise ValueError("Supply the exact supported model, e.g. 2602B")
        if channel not in ("a", "b"):
            raise ValueError("channel must be 'a' or 'b'")
        if channel == "b" and expected_model in self.SINGLE:
            raise ValueError("This model has no channel B")
        self.max_abs_bias_v = finite(max_abs_bias_v, "max_abs_bias_v")
        self.max_current_a = finite(max_current_a, "max_current_a")
        if not 0 <= self.max_abs_bias_v <= 20 or not 0 < self.max_current_a <= 0.1:
            raise ValueError("Wrapper limits: 0..20 V and >0..0.1 A")
        if type(timeout_ms) is not int or not 100 <= timeout_ms <= 120000:
            raise ValueError("timeout_ms must be an integer in 100..120000")
        if not isinstance(resource, str) or not resource.strip():
            raise ValueError("A resource string is required")
        self.resource, self.expected_model = resource, expected_model
        self.channel, self.smu = channel, f"smu{channel}"
        self.timeout_ms, self.visa_library = timeout_ms, visa_library
        self._borrowed = session is not None
        self._session, self._rm = session, None
        self._lock = threading.RLock()
        self._connected = self._identified = self._faulted = False
        self._closed = False
        self._enabled = False
        self.settings = None
        self.identity = ""
        self.line_frequency_hz = None

    def _query(self, command):
        return self._session.query(command).strip()

    def _write(self, command):
        self._session.write(command)

    def _check_errors(self):
        count = number(self._query("print(errorqueue.count)"))
        if count != 0:
            raise InstrumentError(
                f"Instrument error queue has {count:g} entries; inspect it on "
                "the instrument. Entries have NOT been cleared by this wrapper."
            )

    def _require(self):
        if not self._connected or self._faulted:
            raise InstrumentError("Connect a fresh, healthy wrapper first")

    def _trip(self, exc):
        self._faulted, self._enabled, self.settings = True, False, None
        if self._identified and self._session is not None:
            try:
                self._write(f"{self.smu}.source.output = {self.smu}.OUTPUT_HIGH_Z")
            except BaseException as cleanup:  # noqa: BLE001
                exc.add_note(f"Output-off write also failed: {cleanup}")
            exc.add_note(
                "Output-off was attempted, NOT confirmed. Check the instrument locally."
            )

    def connect(self):
        with self._lock:
            if self._connected or self._faulted or self._closed:
                raise InstrumentError("Use a new wrapper for a new connection")
            try:
                if self._session is None:
                    import pyvisa  # Lazy import; offline use requires no VISA.

                    self._rm = pyvisa.ResourceManager(self.visa_library)
                    self._session = self._rm.open_resource(self.resource)
                self._session.timeout = self.timeout_ms
                self._session.read_termination = "\n"
                self._session.write_termination = "\n"
                self.identity = self._query("*IDN?")
                parts = [p.strip().upper() for p in self.identity.split(",")]
                if len(parts) < 2 or "KEITHLEY" not in parts[0]:
                    raise InstrumentError(f"Unexpected identity: {self.identity!r}")
                model = re.sub(r"^MODEL\s+", "", parts[1])
                if model != self.expected_model:
                    raise InstrumentError(
                        f"Expected {self.expected_model}, got {model}"
                    )
                # Refuse an existing sweep rather than aborting someone else's work.
                if (
                    number(self._query("print(status.operation.sweeping.condition)"))
                    != 0
                ):
                    raise InstrumentError(
                        "An instrument sweep is active; ownership refused"
                    )
                self._identified = True
                self._write(f"{self.smu}.source.output = {self.smu}.OUTPUT_HIGH_Z")
                self._check_errors()
                if number(self._query(f"print({self.smu}.source.output)")) != 0:
                    raise InstrumentError("Could not confirm output off")
                self._write("format.data = format.ASCII")
                self._write("format.asciiprecision = 12")
                self.line_frequency_hz = number(
                    self._query("print(localnode.linefreq)")
                )
                if self.line_frequency_hz not in (50, 60):
                    raise InstrumentError("Unexpected line frequency")
                self._check_errors()
                self._connected = True
                return self
            except BaseException as exc:
                self._trip(exc)
                self._release(exc)
                raise

    def configure(self, settings: Settings):
        if not isinstance(settings, Settings):
            raise TypeError("settings must be Settings")
        if abs(settings.bias_v) > self.max_abs_bias_v:
            raise ValueError("Bias exceeds your DUT voltage limit")
        if settings.current_limit_a > self.max_current_a:
            raise ValueError("Compliance exceeds your DUT current limit")
        minimum = 1e-10 if self.expected_model.startswith("263") else 1e-8
        if settings.current_limit_a < minimum:
            raise ValueError("Compliance is below this model family's minimum")
        with self._lock:
            self._require()
            if self._enabled:
                raise InstrumentError("Disable output before reconfiguration")
            self.settings = None
            s = self.smu
            commands = [
                f"{s}.source.output = {s}.OUTPUT_HIGH_Z",
                f"{s}.source.offmode = {s}.OUTPUT_HIGH_Z",
                f"{s}.source.highc = {s}.DISABLE",
                f"{s}.source.func = {s}.OUTPUT_DCVOLTS",
                f"{s}.source.limiti = {settings.current_limit_a:.12g}",
                f"{s}.source.levelv = {settings.bias_v:.12g}",
                f"{s}.source.rangev = {max(abs(settings.bias_v), 0.001):.12g}",
                f"{s}.source.delay = {s}.DELAY_AUTO",
                f"{s}.sense = {s}.SENSE_{'REMOTE' if settings.remote_sense else 'LOCAL'}",
                f"{s}.measure.nplc = {settings.nplc:.12g}",
                f"{s}.measure.count = 1",
                f"{s}.measure.filter.enable = {s}.FILTER_OFF",
                f"{s}.measure.rel.enablei = {s}.REL_OFF",
                f"{s}.measure.rel.enablev = {s}.REL_OFF",
                f"{s}.measure.autozero = {s}.AUTOZERO_AUTO",
                f"{s}.measure.delayfactor = 1",
                f"{s}.measure.delay = {s}.DELAY_AUTO",
            ]
            if self.expected_model.startswith("263"):
                # Avoid the ~1 Hz analog filter smearing adjacent scan pixels.
                commands.append(f"{s}.measure.analogfilter = 0")
            if settings.current_range_a is None:
                low = (
                    1e-10
                    if self.expected_model in {"2635B", "2636B"}
                    else (1e-9 if self.expected_model == "2634B" else 1e-7)
                )
                commands += [
                    f"{s}.measure.lowrangei = {low:.12g}",
                    f"{s}.measure.autorangei = {s}.AUTORANGE_ON",
                ]
            else:
                commands.append(f"{s}.measure.rangei = {settings.current_range_a:.12g}")
            try:
                for command in commands:
                    self._write(command)
                self._check_errors()
                self.settings = settings
                self._verify_settings()
            except BaseException as exc:
                self._trip(exc)
                raise

    def _verify_settings(self):
        s, cfg = self.smu, self.settings
        fields = self._query(
            f"print({s}.source.func, {s}.source.levelv, {s}.source.limiti, "
            f"{s}.measure.nplc, {s}.source.output)"
        ).split()
        if len(fields) != 5:
            raise InstrumentError("Malformed configuration readback")
        func, bias, limit, nplc, output = map(number, fields)
        if func != 1 or output != 0:
            raise InstrumentError("Expected voltage source with output off")
        for actual, wanted in (
            (bias, cfg.bias_v),
            (limit, cfg.current_limit_a),
            (nplc, cfg.nplc),
        ):
            if not math.isclose(actual, wanted, rel_tol=1e-6, abs_tol=1e-15):
                raise InstrumentError("Configuration readback differs from request")
        self._check_errors()

    def enable_output(self):
        with self._lock:
            self._require()
            if self.settings is None or self._enabled:
                raise InstrumentError(
                    "Configure first; output must not already be enabled"
                )
            try:
                self._verify_settings()
                self._write(f"{self.smu}.source.output = {self.smu}.OUTPUT_ON")
                self._check_errors()
                if number(self._query(f"print({self.smu}.source.output)")) != 1:
                    raise InstrumentError("Output enable was not confirmed")
                self._enabled = True
            except BaseException as exc:
                self._trip(exc)
                raise

    def disable_output(self):
        with self._lock:
            self._require()
            try:
                self._write(f"{self.smu}.source.output = {self.smu}.OUTPUT_HIGH_Z")
                if number(self._query(f"print({self.smu}.source.output)")) != 0:
                    raise InstrumentError("Output-off readback failed")
                self._check_errors()
                self._enabled = False
            except BaseException as exc:
                self._trip(exc)
                raise

    @contextmanager
    def output_enabled(self):
        self.enable_output()
        try:
            yield self
        except BaseException as exc:
            with self._lock:
                self._trip(exc)
            raise
        else:
            self.disable_output()

    def read(self) -> Reading:
        """Acquire one current/voltage reading. No motion, settling or averaging."""
        with self._lock:
            self._require()
            if not self._enabled:
                raise InstrumentError("Output must be explicitly enabled")
            try:
                s = self.smu
                started = time.time()
                response = self._query(
                    f"local i,v = {s}.measure.iv(); "
                    f"print(i,v,{s}.source.compliance,"
                    f"{s}.source.output,{s}.measure.rangei)"
                )
                finished = time.time()
                fields = response.split()
                if len(fields) != 5 or fields[2] not in ("true", "false"):
                    raise InstrumentError(f"Malformed measurement: {response!r}")
                current = number(fields[0])
                voltage = number(fields[1])
                output = number(fields[3])
                current_range = number(fields[4])
                if fields[2] == "true":
                    raise InstrumentError("Compliance reached: reading rejected")
                if output != 1 or current_range <= 0:
                    raise InstrumentError("Output off or invalid current range")
                self._check_errors()
                return Reading(current, voltage, current_range, started, finished)
            except BaseException as exc:
                self._trip(exc)
                raise

    def _batch_command(self, samples):
        """A bounded, local TSP loop. No globals, resets, buffer clearing or enabling.

        Checks compliance/output/error queue before and after EVERY conversion.
        Any detected failure discards the batch and attempts high-Z on instrument.
        Runtime exceptions are caught by pcall; a host failure also uses _trip().
        """
        s = self.smu
        return (
            'local function spb_acquire() local rows = ""; '
            f"for k=1,{samples} do "
            f"if {s}.source.output ~= 1 then error('output_off') end; "
            f"if {s}.source.compliance then error('compliance') end; "
            "if errorqueue.count ~= 0 then error('instrument_errors') end; "
            f"local i,v = {s}.measure.iv(); "
            f"local r = {s}.measure.rangei; "
            f"if {s}.source.compliance then error('compliance') end; "
            f"if {s}.source.output ~= 1 then error('output_off') end; "
            "if errorqueue.count ~= 0 then error('instrument_errors') end; "
            "if i ~= i or v ~= v or r ~= r or math.abs(i) >= 9e37 "
            "or math.abs(v) >= 9e37 or r <= 0 or r >= 9e37 then error('invalid_reading') end; "
            "rows = rows .. string.format(' %.12g %.12g %.12g',i,v,r); end; return rows; end; "
            "local ok,rows = pcall(spb_acquire); "
            f"if not ok then {s}.source.output = {s}.OUTPUT_HIGH_Z; "
            "print('SPB1 ERR batch_failed'); "
            f"else print('SPB1 OK {samples}' .. rows) end"
        )

    def read_many(self, samples, *, cancel=None):
        """Acquire raw readings in batches of at most three, without averaging.

        Existing NPLC, autozero, delays, filters and ranges are unchanged. Stop is
        checked between batches, not during an instrument-side batch. Each reading
        carries the SAME batch host bounds, explicitly labelled as such; no per-
        conversion host or hardware timestamp is invented. Timeout is unchanged.
        """
        if type(samples) is not int or not 1 <= samples <= 1000:
            raise ValueError("samples must be an integer in 1..1000")
        with self._lock:
            self._require()
            if not self._enabled:
                raise InstrumentError("Output must be explicitly enabled")
            readings = []
            try:
                while len(readings) < samples:
                    if cancel is not None and cancel.is_set():
                        raise AcquisitionCancelled("Cancelled before measurement batch")
                    size = min(3, samples - len(readings))
                    started = time.time()
                    response = self._query(self._batch_command(size))
                    finished = time.time()
                    fields = response.split()
                    if (
                        fields[:3] != ["SPB1", "OK", str(size)]
                        or len(fields) != 3 + size * 3
                    ):
                        raise InstrumentError(
                            f"Rejected measurement batch: {response!r}"
                        )
                    batch = []
                    for index in range(size):
                        current, voltage, current_range = map(
                            number, fields[3 + 3 * index : 6 + 3 * index]
                        )
                        if current_range <= 0:
                            raise InstrumentError(
                                "Invalid current range in measurement batch"
                            )
                        batch.append(
                            Reading(
                                current,
                                voltage,
                                current_range,
                                started,
                                finished,
                                "host_batch_bounds_not_individual_conversion_times",
                            )
                        )
                    if cancel is not None and cancel.is_set():
                        raise AcquisitionCancelled("Cancelled after measurement batch")
                    readings.extend(batch)
                return tuple(readings)
            except BaseException as exc:
                self._trip(exc)
                raise

    def _release(self, primary=None):
        failures = []
        if not self._borrowed and self._session is not None:
            try:
                self._session.close()
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)
        if self._rm is not None:
            try:
                self._rm.close()
            except BaseException as exc:  # noqa: BLE001
                failures.append(exc)
        self._session, self._rm, self._connected = None, None, False
        self._closed = True
        for failure in failures:
            if primary is not None:
                primary.add_note(f"Resource close failed: {failure}")
        if failures and primary is None:
            raise InstrumentError(f"Resource close failed: {failures}")

    def close(self):
        with self._lock:
            primary = None
            try:
                if self._connected and self._identified:
                    if self._faulted:
                        self._write(
                            f"{self.smu}.source.output = {self.smu}.OUTPUT_HIGH_Z"
                        )
                        raise InstrumentError(
                            "Faulted session closed; output-off NOT confirmed"
                        )
                    self.disable_output()
            except BaseException as exc:
                primary = exc
                raise
            finally:
                self._release(primary)
                self.settings, self._enabled = None, False

    def __enter__(self):
        return self.connect()

    def __exit__(self, kind, exc, traceback):
        try:
            self.close()
        except BaseException as cleanup:
            if exc is None:
                raise
            exc.add_note(f"Cleanup: {cleanup}")
        return False
