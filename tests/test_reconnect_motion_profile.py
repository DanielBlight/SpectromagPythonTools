"""Synthetic lifecycle and profiling regressions. No DLLs or hardware used."""

import json
import threading
from types import SimpleNamespace

import pytest
from test_commissioning import session_fixture, wait_idle
from test_mirror_fast_policy import Fixture, move

from spectromagpythontools.devices.pgm1se import GimbalError, KinesisPPC102, RawState
from spectromagpythontools.gui.control import HardwareWorker
from spectromagpythontools.routines.motion_profile import capture_motion
from spectromagpythontools.scripts.map_timing import summarise


def states(bits):
    return tuple(RawState(c, 0, bits, 2, 0, 1, 0) for c in (1, 2))


def test_startup_refresh_waits_for_both_actuators():
    backend = KinesisPPC102()
    observations = iter([states(0), (states(1)[0], states(0)[1]), states(1)])
    calls = []
    backend.read_channels = lambda channels: (
        calls.append(channels) or next(observations)
    )
    backend._wait_connected()
    assert calls == [(1, 2)] * 3


def test_startup_missing_actuator_is_bounded():
    backend = KinesisPPC102()
    calls = []
    backend.read_channels = lambda channels: calls.append(channels) or states(0)
    with pytest.raises(GimbalError, match="startup refresh"):
        backend._wait_connected()
    assert len(calls) == 10


def test_startup_deadline_stops_retry(monkeypatch):
    from spectromagpythontools.devices import pgm1se

    clock = iter([0, 3])
    monkeypatch.setattr(pgm1se.time, "monotonic", lambda: next(clock))
    backend = KinesisPPC102()
    backend.read_channels = lambda channels: states(0)
    with pytest.raises(GimbalError, match="startup refresh"):
        backend._wait_connected()


@pytest.mark.parametrize("bit", [0x800, 0x1000, 0x2000, 0x40000000])
def test_startup_fault_is_not_retried(bit):
    backend = KinesisPPC102()
    calls = []
    backend.read_channels = lambda channels: calls.append(channels) or states(1 | bit)
    with pytest.raises(GimbalError, match="fault"):
        backend._wait_connected()
    assert len(calls) == 1


@pytest.mark.parametrize("fail_close", [False, True])
def test_backend_close_clears_python_state_even_on_failure(fail_close):
    backend = KinesisPPC102()
    calls = []

    def close(serial):
        calls.append(serial)
        if fail_close:
            raise OSError("synthetic close failure")

    backend._dll = SimpleNamespace(PPC_Close=close)
    backend._serial = b"95000001"
    backend._directory_handle = SimpleNamespace(close=lambda: calls.append("directory"))
    backend._motion_settings = {1: (0, 1)}
    backend._settings_checked_at = 123
    if fail_close:
        with pytest.raises(OSError, match="close failure"):
            backend.close()
    else:
        backend.close()
    assert backend._dll is backend._serial is backend._directory_handle is None
    assert backend._motion_settings == {} and backend._settings_checked_at == 0
    backend.close()
    assert calls == [b"95000001", "directory"]


def test_failed_native_open_releases_partial_resources():
    backend = KinesisPPC102()
    calls = []

    def fail(serial):
        backend._serial = serial.encode()
        backend._dll = SimpleNamespace(PPC_Close=lambda s: calls.append(s))
        raise OSError("synthetic startup failure")

    backend._open_device = fail
    with pytest.raises(OSError, match="startup failure"):
        backend.open("95000001")
    assert calls == [b"95000001"]
    assert backend._serial is backend._dll is None


def test_backend_reopens_with_clean_cache_in_same_process():
    backend = KinesisPPC102()
    calls = []

    def opened(serial):
        assert backend._motion_settings == {} and backend._settings_checked_at == 0
        backend._serial = serial.encode()
        backend._dll = SimpleNamespace(PPC_Close=lambda s: calls.append(s))
        backend._motion_settings = {1: (0, 1)}
        return "PPC102"

    backend._open_device = opened
    for _ in range(2):
        assert backend.open("95000001") == "PPC102"
        backend.close()
    assert len(calls) == 2


def test_failed_session_inspection_closes_candidate(tmp_path):
    session, cfg, native, _, _ = session_fixture(tmp_path)
    native.read_error = True
    with pytest.raises(OSError, match="read failed"):
        session.connect(cfg, threading.Event())
    assert session.mount is None and not session.prepared
    assert native.calls[-1] == ("close",)


def test_worker_cooperative_shutdown_while_busy():
    entered, release = threading.Event(), threading.Event()
    closed = []

    class BlockingSession:
        prepared = False

        def __init__(self, emit):
            pass

        def inspect(self, config, cancel):
            entered.set()
            assert release.wait(3)
            assert cancel.is_set()

        def disconnect(self, config, cancel):
            closed.append(threading.get_ident())

        def state(self):
            return {"connected": False, "prepared": False}

    worker = HardwareWorker(BlockingSession)
    try:
        worker.submit("inspect", {})
        assert entered.wait(2)
        worker.request_shutdown()
        with pytest.raises(RuntimeError):
            worker.submit("connect", {})
    finally:
        release.set()
        worker._thread.join(3)
    assert not worker._thread.is_alive() and len(closed) == 1


def test_two_worker_sessions_close_before_reopen(tmp_path):
    for _ in range(2):
        session, cfg, native, _, _ = session_fixture(tmp_path)
        worker = HardwareWorker(lambda emit, session=session: session)
        try:
            worker.submit("connect", cfg)
            wait_idle(worker)
            worker.submit("prepare", cfg)
            wait_idle(worker)
            assert session.prepared
        finally:
            worker.request_shutdown()
            worker._thread.join(3)
        assert not worker._thread.is_alive()
        assert native.calls[-1] == ("close",)
        assert session.mount is None


def test_launch_mainloop_error_still_joins(monkeypatch):
    import sys

    from spectromagpythontools.gui import commissioning

    calls = []

    def fail():
        raise RuntimeError("synthetic Tk exit")

    root = SimpleNamespace(mainloop=fail)
    worker = SimpleNamespace(
        request_shutdown=lambda: calls.append("shutdown"),
        wait_stopped=lambda: calls.append("join"),
    )
    app = SimpleNamespace(
        worker=worker, log_file=None, tabs=SimpleNamespace(select=lambda index: None)
    )
    monkeypatch.setitem(sys.modules, "tkinter", SimpleNamespace(Tk=lambda: root))
    monkeypatch.setattr(commissioning, "Application", lambda *args: app)
    with pytest.raises(RuntimeError, match="Tk exit"):
        commissioning.launch("unused")
    assert calls == ["shutdown", "join"]


def fast_environment(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.05")
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")


def test_profile_has_one_record_without_extra_reads(monkeypatch, tmp_path):
    fast_environment(monkeypatch)
    mount = Fixture()
    path = tmp_path / "map.motion.txt"
    with capture_motion(mount, path):
        move(mount)
    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["status"] == "complete" and not record["fallback"]
    assert record["command_calls"] == 1 and record["post_read_calls"] == 1
    assert mount.full == 1 and mount.fast == 1
    assert record["total_s"] >= record["delay_s"]
    assert "tolerance_check_s" in record
    assert not hasattr(mount, "_motion_profile_records")


@pytest.mark.parametrize("fail", [False, True])
def test_profile_records_fallback_success_and_failure(monkeypatch, tmp_path, fail):
    fast_environment(monkeypatch)
    mount = Fixture()
    mount.miss = True
    if fail:

        def fallback(*args, **kwargs):
            raise RuntimeError("synthetic fallback failure")

        mount._verified_move_and_wait = fallback
    path = tmp_path / "fallback.motion.txt"
    if fail:
        with (
            pytest.raises(RuntimeError, match="fallback failure"),
            capture_motion(mount, path),
        ):
            move(mount)
    else:
        with capture_motion(mount, path):
            move(mount)
    record = json.loads(path.read_text())
    assert record["fallback"] and record["fallback_calls"] == 1
    assert record["fallback_s"] >= 0
    assert record["status"] == ("error" if fail else "complete")
    assert not mount._fast_continue_settling


def test_profile_records_cancel_before_commands(monkeypatch, tmp_path):
    fast_environment(monkeypatch)
    mount = Fixture()
    cancel = threading.Event()
    cancel.set()
    path = tmp_path / "cancel.motion.txt"
    from spectromagpythontools.devices.pgm1se import MoveCancelled

    with pytest.raises(MoveCancelled), capture_motion(mount, path):
        move(mount, cancel=cancel)
    assert json.loads(path.read_text())["status"] == "error"
    assert not mount.commands


def test_gui_map_sidecar_and_summary(monkeypatch, tmp_path):
    fast_environment(monkeypatch)
    session, cfg, _, _, _ = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    try:
        result = session.map(cfg, cancel)
        summary = summarise(result["output_csv"])
        profile = summary["motion_profile"]
        assert profile["moves"] == 4 and profile["fallback_count"] == 0
        assert profile["failed_moves"] == 0
        assert profile["phase_totals_s"]["post_read_s"] >= 0
    finally:
        session.disconnect({}, cancel)


def test_sidecar_collision_prevents_smu_and_motion(tmp_path):
    from pathlib import Path

    session, cfg, native, transport, _ = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    path = Path(cfg["output_csv"]).with_suffix(".motion.txt")
    path.write_text("keep")
    try:
        with pytest.raises(FileExistsError):
            session.map(cfg, cancel)
        assert not transport.log
        assert not any(call[0] == "set" for call in native.calls)
        assert path.read_text() == "keep"
    finally:
        session.disconnect({}, cancel)
