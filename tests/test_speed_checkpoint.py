"""Offline speed-checkpoint regressions. All device values are synthetic."""

import csv
import io
import json
import threading
from dataclasses import replace
from types import SimpleNamespace

import pytest
from test_keithley2600 import make
from test_pgm1se import ready

from spectromagpythontools.devices.keithley2600 import (
    AcquisitionCancelled,
    InstrumentError,
)
from spectromagpythontools.devices.pgm1se import GimbalError, KinesisPPC102
from spectromagpythontools.routines.photocurrent_map import read_point, scan_map


def batch_queries(session):
    return [
        command
        for command in session.log
        if command.startswith("local function spb_acquire()")
    ]


def test_three_readings_one_transaction_same_statistics():
    smu, session = make()
    with smu.output_enabled():
        point = read_point(smu, samples=3)
    assert len(point.readings) == 3
    assert len(batch_queries(session)) == 1
    assert point.mean_current_a == pytest.approx(2e-9)
    assert point.sample_std_current_a == 0
    assert session.v["source.output"] == 0
    assert len({reading.host_started_unix_s for reading in point.readings}) == 1
    assert all("batch_bounds" in reading.timestamp_basis for reading in point.readings)
    assert point.acquisition_elapsed_s >= 0 and point.settle_elapsed_s >= 0


def test_nonconstant_samples_keep_mean_and_sample_std():
    smu, session = make()
    original = session.query

    def query(command):
        if command.startswith("local function spb_acquire()"):
            session.log.append(command)
            return "SPB1 OK 3 1e-9 0.5 1e-8 2e-9 0.5 1e-8 3e-9 0.5 1e-8"
        return original(command)

    session.query = query
    with smu.output_enabled():
        point = read_point(smu, samples=3)
    assert [r.current_a for r in point.readings] == [1e-9, 2e-9, 3e-9]
    assert point.mean_current_a == pytest.approx(2e-9)
    assert point.sample_std_current_a == pytest.approx(1e-9)
    assert point.mean_voltage_v == 0.5


def test_single_read_stays_legacy():
    smu, session = make()
    with smu.output_enabled():
        point = read_point(smu, samples=1)
    assert not batch_queries(session)
    assert point.readings[0].timestamp_basis == "host_single_read_bounds"
    assert point.sample_std_current_a is None


def test_chunks_are_bounded_to_three():
    smu, session = make()
    with smu.output_enabled():
        point = read_point(smu, samples=8)
    assert len(point.readings) == 8
    commands = batch_queries(session)
    assert len(commands) == 3
    assert [f"for k=1,{n} do" in c for n, c in zip((3, 3, 2), commands)] == [True] * 3


@pytest.mark.parametrize("samples", [0, -1, 1001, True, 1.5])
def test_bad_batch_size_before_io(samples):
    smu, session = make()
    before = list(session.log)
    with pytest.raises(ValueError):
        smu.read_many(samples)
    assert session.log == before


def test_batch_requires_enabled_output():
    smu, session = make()
    before = list(session.log)
    with pytest.raises(InstrumentError, match="enabled"):
        smu.read_many(3)
    assert session.log == before


@pytest.mark.parametrize(
    "response",
    [
        "SPB1 ERR batch_failed",
        "SPB1 OK 2 1 2 3",
        "SPB1 OK 3 1 2 3",
        "SPB1 OK 3 nan 0 1 1 0 1 1 0 1",
        "SPB1 OK 3 9.91e37 0 1 1 0 1 1 0 1",
        "SPB1 OK 3 1 0 -1 1 0 1 1 0 1",
        "SPB1 OK 3 1 0 0 1 0 1 1 0 1",
    ],
)
def test_bad_batch_faults_and_attempts_high_z(response):
    smu, session = make()
    smu.enable_output()
    original = session.query
    session.query = lambda c: (
        response if c.startswith("local function spb_acquire()") else original(c)
    )
    with pytest.raises((InstrumentError, ValueError)):
        read_point(smu, samples=3)
    assert session.v["source.output"] == 0
    assert smu._faulted


def test_batch_timeout_faults_no_retry():
    smu, session = make()
    smu.enable_output()
    session.failure = TimeoutError("synthetic timeout")
    with pytest.raises(TimeoutError):
        read_point(smu, samples=3)
    assert len(batch_queries(session)) == 1
    assert session.v["source.output"] == 0


def test_cancel_between_batches_discards_pixel():
    smu, session = make()
    smu.enable_output()
    event = threading.Event()
    original = session.query

    def query(command):
        result = original(command)
        if command.startswith("local function spb_acquire()"):
            event.set()
        return result

    session.query = query
    with pytest.raises(AcquisitionCancelled):
        read_point(smu, samples=6, cancel=event)
    assert len(batch_queries(session)) == 1
    assert session.v["source.output"] == 0


def test_cancel_before_batch_sends_no_measurement():
    smu, session = make()
    smu.enable_output()
    event = threading.Event()
    event.set()
    with pytest.raises(AcquisitionCancelled):
        read_point(smu, samples=3, cancel=event)
    assert not batch_queries(session)
    assert session.v["source.output"] == 0


def test_batch_map_csv_timings_and_raw_samples(tmp_path):
    smu, session = make()
    path = tmp_path / "map.csv"
    assert (
        scan_map(
            smu,
            xs=[0, 1],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=path,
            samples=3,
            settle_s=0,
        )
        == 2
    )
    lines = path.read_text().splitlines()
    assert json.loads(lines[0][2:])["schema"] == "photocurrent-map-v2"
    footer = json.loads(lines[-1][2:])
    assert footer["status"] == "complete"
    assert all(value >= 0 for value in footer["timing_totals_s"].values())
    rows = list(
        csv.DictReader(
            io.StringIO("\n".join(line for line in lines if not line.startswith("#")))
        )
    )
    assert len(rows) == 2
    assert all(len(json.loads(row["readings_json"])) == 3 for row in rows)
    assert all(float(row["motion_s"]) >= 0 for row in rows)
    assert all(float(row["acquisition_s"]) >= 0 for row in rows)
    assert len(batch_queries(session)) == 2


def test_batch_map_partial_and_output_off(tmp_path):
    smu, session = make()
    path = tmp_path / "map.csv"
    event = threading.Event()
    with pytest.raises(AcquisitionCancelled):
        scan_map(
            smu,
            xs=[0, 1],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=path,
            samples=3,
            settle_s=0,
            cancel=event,
            on_pixel=lambda pixel: event.set(),
        )
    footer = json.loads(path.read_text().splitlines()[-1][2:])
    assert footer["status"] == "partial" and footer["pixels"] == 1
    assert "timing_totals_s" in footer
    assert session.v["source.output"] == 0


def native_backend(monkeypatch):
    from spectromagpythontools.devices import pgm1se

    calls = []
    clock = [10.0]
    monkeypatch.setattr(pgm1se.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(
        pgm1se.time, "sleep", lambda seconds: calls.append(("sleep", seconds))
    )
    backend = KinesisPPC102()
    backend._serial = b"95000001"
    source = [0]

    def request(name):
        return lambda serial, channel: calls.append((name, channel)) or 0

    def get_io(serial, channel, pointer):
        calls.append(("io", channel))
        pointer._obj.control_source = source[0]
        pointer._obj.feedback_source = 1
        return 0

    backend._dll = SimpleNamespace(
        PPC2_RequestSettings=request("settings"),
        PPC2_RequestStatus=request("status"),
        PPC2_RequestActualPosition=request("position"),
        PPC2_GetIOSettings=get_io,
        PPC2_GetPosition=lambda serial, channel: channel,
        PPC2_GetStatusBits=lambda serial, channel: 0x80000401,
        PPC2_GetPositionControlMode=lambda serial, channel: 2,
    )
    return backend, calls, clock, source


def test_fast_poll_omits_settings_and_io_but_keeps_live_requests(monkeypatch):
    backend, calls, _clock, _source = native_backend(monkeypatch)
    backend.read_channels((1, 2))
    calls.clear()
    states = backend.read_motion_channels((1, 2))
    assert [s.channel for s in states] == [1, 2]
    assert all(s.feedback_source == 1 for s in states)
    assert not any(c[0] in ("settings", "io") for c in calls)
    assert calls == [
        ("status", 1),
        ("position", 1),
        ("status", 2),
        ("position", 2),
        ("sleep", 0.1),
    ]


def test_fast_poll_periodically_refreshes_configuration(monkeypatch):
    backend, calls, clock, source = native_backend(monkeypatch)
    backend.read_channels((1, 2))
    calls.clear()
    source[0] = 1
    clock[0] += 1.01
    states = backend.read_motion_channels((1, 2))
    assert all(s.input_source == 1 for s in states)
    assert len([c for c in calls if c[0] == "settings"]) == 2
    assert len([c for c in calls if c[0] == "io"]) == 2


def test_fast_poll_initialises_with_full_read(monkeypatch):
    backend, calls, _clock, _source = native_backend(monkeypatch)
    backend.read_motion_channels((1, 2))
    assert len([c for c in calls if c[0] == "settings"]) == 2


@pytest.mark.parametrize("channels", [(), (1, 1), (0, 2), (True, 2)])
def test_fast_poll_invalid_channels(monkeypatch, channels):
    backend, calls, _clock, _source = native_backend(monkeypatch)
    with pytest.raises(ValueError):
        backend.read_motion_channels(channels)
    assert calls == []


def test_motion_uses_fast_reader_after_full_precheck():
    mount, backend = ready()
    calls = []

    def fast(channels):
        calls.append(tuple(channels))
        return tuple(backend.read(c) for c in channels)

    backend.read_motion_channels = fast
    assert mount.move_and_wait(1, -1, stable_s=0.05) == pytest.approx((1, -1))
    assert len(calls) >= 2


def test_fault_during_fast_poll_stops_move():
    mount, backend = ready()

    def fast(channels):
        return tuple(
            replace(backend.read(c), status_bits=backend.bits | 0x40000000)
            for c in channels
        )

    backend.read_motion_channels = fast
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, -1, stable_s=0.05)
    assert mount._faulted and not mount._prepared


def test_full_precheck_still_prevents_commands():
    mount, backend = ready()
    backend.source = 1
    backend.read_motion_channels = lambda channels: pytest.fail(
        "fast path must not precede full check"
    )
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, 1, stable_s=0.05)
    assert not any(c[0] == "set" for c in backend.calls)


def test_feedback_missing_rejected_even_without_generic_bit():
    mount, backend = ready()
    backend.bits &= ~0x100
    original = backend.read
    backend.read = lambda c: replace(original(c), feedback_source=0)
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, 1, stable_s=0.05)
    assert not any(c[0] == "set" for c in backend.calls)


def test_timing_summary_confirms_averaging(tmp_path):
    from spectromagpythontools.scripts.map_timing import summarise

    smu, _session = make()
    path = tmp_path / "map.csv"
    scan_map(
        smu,
        xs=[0],
        ys=[0],
        move_and_wait=lambda x, y: (x, y),
        output_csv=path,
        samples=3,
        settle_s=0,
    )
    result = summarise(path)
    assert result["status"] == "complete"
    assert result["pixels"] == 1
    assert result["raw_readings_per_pixel"] == [3]
    assert result["mean_pixel_motion_s"] >= 0
    assert result["mean_pixel_acquisition_s"] >= 0
