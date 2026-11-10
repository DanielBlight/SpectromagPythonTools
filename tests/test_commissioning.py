"""Offline tests only. Synthetic instrument fixtures are NOT measured data."""

import importlib
import json
import queue
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from test_keithley2600 import Fake, make
from test_pgm1se import FakeBackend

from spectromagpythontools.configuration import (
    axis_values,
    build_mount,
    build_smu,
    calibration,
    integer,
    load_config,
    measurement,
)
from spectromagpythontools.devices.keithley2600 import (
    AcquisitionCancelled,
    KeithleyPhotocurrent,
)
from spectromagpythontools.gui.control import HardwareWorker, Session
from spectromagpythontools.routines.photocurrent_map import scan_map


def config(tmp_path):
    return {
        "pgm1se": {
            "serial": "95000001",
            "max_abs_mrad": 1,
            "x_calibration": {
                "raw_minus_10": -10000,
                "raw_plus_10": 10000,
                "verified": True,
                "evidence": "SYNTHETIC TEST FIXTURE",
            },
            "y_calibration": {
                "raw_minus_10": -10000,
                "raw_plus_10": 10000,
                "verified": True,
                "evidence": "SYNTHETIC TEST FIXTURE",
            },
        },
        "keithley": {"resource": "USB0::TEST::INSTR", "model": "2602B", "channel": "a"},
        "x_start": -0.01,
        "x_end": 0.01,
        "nx": 2,
        "y_start": -0.01,
        "y_end": 0.01,
        "ny": 2,
        "max_abs_bias_v": 1,
        "max_current_a": 1e-5,
        "bias_v": 0,
        "current_limit_a": 1e-6,
        "nplc": 1,
        "samples": 1,
        "settle_s": 0,
        "mirror_tolerance_mrad": 0.01,
        "mirror_stable_s": 0.05,
        "mirror_timeout_s": 2,
        "output_csv": str(tmp_path / "map.csv"),
        "commissioning_ack": True,
    }


def session_fixture(tmp_path, emit=None):
    cfg = config(tmp_path)
    native = FakeBackend()
    transport = Fake()
    events = []

    def smu_factory(section, **limits):
        return KeithleyPhotocurrent(
            section["resource"], expected_model="2602B", session=transport, **limits
        )

    session = Session(
        emit or (lambda kind, payload: events.append((kind, payload))),
        mount_factory=lambda section: build_mount(section, backend=native),
        smu_factory=smu_factory,
    )
    return session, cfg, native, transport, events


@pytest.mark.parametrize("args", [(0, 0, 1), (-1, 1, 3), (1, -1, 3)])
def test_axis_grid(args):
    values = axis_values(*args)
    assert values[0] == args[0] and values[-1] == args[1]


@pytest.mark.parametrize(
    "args", [(0, 1, 1), (0, 0, 2), (-11, 0, 2), (0, 1, 101), (0, 1, 2.5)]
)
def test_bad_grid(args):
    with pytest.raises((ValueError, TypeError)):
        axis_values(*args)


@pytest.mark.parametrize("value", [True, float("nan"), 1.5])
def test_bad_integer(value):
    with pytest.raises((ValueError, TypeError)):
        integer(value, "test", 0, 10)


def test_placeholder_rejected_without_io():
    with pytest.raises(ValueError):
        build_mount({"serial": "95XXXXXX"})
    with pytest.raises(ValueError):
        build_smu(
            {"resource": "USB0::PLACEHOLDER::INSTR"},
            max_abs_bias_v=0,
            max_current_a=1e-6,
        )


def test_calibration_explicitly_verified():
    with pytest.raises(ValueError):
        calibration({"x_calibration": {"verified": False}}, "x")
    assert calibration({}, "x") is None


def test_measurement_limits(tmp_path):
    cfg = config(tmp_path)
    cfg["bias_v"] = 2
    with pytest.raises(ValueError):
        measurement(cfg)
    cfg["bias_v"] = 0
    cfg["current_limit_a"] = 1
    with pytest.raises(ValueError):
        measurement(cfg)


def test_toml_loader(tmp_path):
    path = tmp_path / "instruments.local.toml"
    path.write_text('[pgm1se]\nserial="95XXXXXX"\n')
    assert load_config(path)["pgm1se"]["serial"] == "95XXXXXX"
    path.write_text("pgm1se=1\n")
    with pytest.raises(ValueError):
        load_config(path)


def test_session_map_and_metadata(tmp_path):
    session, cfg, native, transport, events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    result = session.map(cfg, cancel)
    assert result["pixels"] == 4
    pixels = [p for kind, p in events if kind == "pixel"]
    assert [p["column"] for p in pixels] == [0, 1, 1, 0]
    assert transport.v["source.output"] == 0
    lines = Path(cfg["output_csv"]).read_text().splitlines()
    metadata = json.loads(lines[0][2:])
    assert metadata["position_unit"] == "mrad"
    assert metadata["acquisition"]["mirror"]["serial"] == "95000001"
    assert metadata["acquisition"]["dark_subtracted"] is False
    assert json.loads(lines[-1][2:])["status"] == "complete"
    session.disconnect({}, cancel)
    assert native.calls[-1] == ("close",)


def test_map_collision_before_smu_connection(tmp_path):
    session, cfg, native, transport, _events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    Path(cfg["output_csv"]).write_text("keep")
    with pytest.raises(FileExistsError):
        session.map(cfg, cancel)
    assert transport.log == []
    assert not any(call[0] == "set" for call in native.calls)


def test_map_range_before_smu_connection(tmp_path):
    session, cfg, _native, transport, _events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    cfg["x_end"] = 2
    with pytest.raises(ValueError):
        session.map(cfg, cancel)
    assert transport.log == []


def test_map_requires_ack(tmp_path):
    session, cfg, _native, transport, _events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    cfg["commissioning_ack"] = False
    with pytest.raises(ValueError):
        session.map(cfg, cancel)
    assert transport.log == []


def test_map_cancel_retains_pixel_and_disables(tmp_path):
    cancel = threading.Event()

    def emit(kind, payload):
        if kind == "pixel":
            cancel.set()

    session, cfg, _native, transport, _events = session_fixture(tmp_path, emit)
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    with pytest.raises(AcquisitionCancelled):
        session.map(cfg, cancel)
    assert transport.v["source.output"] == 0
    footer = json.loads(Path(cfg["output_csv"]).read_text().splitlines()[-1][2:])
    assert footer["status"] == "partial" and footer["pixels"] == 1


def test_current_only_does_not_connect_mirror(tmp_path):
    session, cfg, native, transport, _events = session_fixture(tmp_path)
    result = session.read_current(cfg, threading.Event())
    assert result["point"]["mean_current_a"] == pytest.approx(2e-9)
    assert native.calls == []
    assert transport.v["source.output"] == 0


def test_jog_from_readback(tmp_path):
    session, cfg, native, _transport, _events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    native.positions = {1: 100, 2: -100}
    cfg.update(x=0, y=0, dx=0.01, dy=0)
    result = session.move(cfg, cancel)
    assert result["position"] == pytest.approx((0.11, -0.1))


def test_callback_error_marks_partial_and_disables(tmp_path):
    smu, transport = make()

    def callback(pixel):
        raise RuntimeError("callback failed")

    path = tmp_path / "map.csv"
    with pytest.raises(RuntimeError, match="callback failed"):
        scan_map(
            smu,
            xs=[0],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=path,
            settle_s=0,
            on_pixel=callback,
        )
    assert transport.v["source.output"] == 0
    footer = json.loads(path.read_text().splitlines()[-1][2:])
    assert footer["status"] == "partial" and footer["pixels"] == 1


def test_invalid_metadata_before_output_file(tmp_path):
    smu, transport = make()
    path = tmp_path / "map.csv"
    with pytest.raises(ValueError):
        scan_map(
            smu,
            xs=[0],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=path,
            acquisition_metadata={"bad": float("nan")},
        )
    assert not path.exists()
    assert not any("OUTPUT_ON" in command for command in transport.log)


def test_worker_exclusive_and_shutdown():
    entered, release = threading.Event(), threading.Event()
    thread_ids = []

    class BlockingSession:
        prepared = False

        def __init__(self, emit):
            pass

        def state(self):
            return {"connected": False, "prepared": False}

        def inspect(self, cfg, cancel):
            thread_ids.append(threading.get_ident())
            entered.set()
            assert release.wait(3)
            return cfg

        def disconnect(self, cfg, cancel):
            thread_ids.append(threading.get_ident())
            return {}

    worker = HardwareWorker(BlockingSession)
    try:
        worker.submit("inspect", {"value": 1})
        assert entered.wait(2)
        with pytest.raises(RuntimeError):
            worker.submit("move", {})
        worker.cancel.set()
        assert worker.cancel.is_set()
        release.set()
        wait_idle(worker)
        worker.submit("shutdown", {})
        worker._thread.join(3)
        assert not worker._thread.is_alive()
        assert len(set(thread_ids)) == 1 and thread_ids[0] != threading.get_ident()
    finally:
        release.set()
        if worker._thread.is_alive():
            wait_idle(worker)
            worker.submit("shutdown", {})
            worker._thread.join(3)


def wait_idle(worker):
    deadline = time.monotonic() + 4
    while worker.busy:
        if time.monotonic() > deadline:
            raise AssertionError("Worker did not become idle")
        time.sleep(0.01)


def test_worker_error_notes_and_recovery(tmp_path):
    session, cfg, _native, _transport, _events = session_fixture(tmp_path)
    worker = HardwareWorker(lambda emit: session)
    try:
        worker.submit("prepare", cfg)
        wait_idle(worker)
        found = []
        while not worker.events.empty():
            found.append(worker.events.get_nowait())
        error = next(value for kind, value in found if kind == "error")
        assert "Connect" in error["message"]
        worker.submit("shutdown", {})
        worker._thread.join(3)
        assert not worker._thread.is_alive()
    finally:
        if worker._thread.is_alive():
            wait_idle(worker)
            worker.submit("shutdown", {})
            worker._thread.join(3)


def test_imports_do_not_require_tk_or_visa():
    for name in (
        "spectromagpythontools.gui.commissioning",
        "spectromagpythontools.scripts.commissioning_gui",
    ):
        assert importlib.import_module(name) is not None


class FakeVariable:
    def __init__(self, value=None, **kwargs):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    """Headless widget contract fixture, NOT a Tk rendering test."""

    def __init__(self, *args, **kwargs):
        self.options = dict(kwargs)
        self.destroyed = False

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def index(self, *args):
        return "1.0"

    def winfo_width(self):
        return 450

    def winfo_height(self):
        return 420

    def destroy(self):
        self.destroyed = True

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def fake_tk(monkeypatch):
    tk = ModuleType("tkinter")
    for name in ("Frame", "Text", "Canvas", "Tk"):
        setattr(tk, name, FakeWidget)
    tk.StringVar = tk.BooleanVar = FakeVariable
    ttk = SimpleNamespace(
        **{
            name: FakeWidget
            for name in (
                "Frame",
                "Label",
                "Entry",
                "Button",
                "Notebook",
                "Checkbutton",
                "Progressbar",
                "Scrollbar",
            )
        }
    )
    errors = []
    tk.ttk = ttk
    tk.filedialog = SimpleNamespace(
        askopenfilename=lambda **kwargs: "", asksaveasfilename=lambda **kwargs: ""
    )
    tk.messagebox = SimpleNamespace(
        showerror=lambda *args: errors.append(args), askyesno=lambda *args: True
    )
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    return errors


def test_ui_build_state_progress_and_close(monkeypatch, tmp_path):
    errors = fake_tk(monkeypatch)
    from spectromagpythontools.gui.commissioning import Application

    path = tmp_path / "config" / "instruments.local.toml"
    path.parent.mkdir()
    path.write_text(
        '[pgm1se]\nserial="95XXXXXX"\n[keithley]\nresource="USB0::PLACEHOLDER::INSTR"\n'
    )
    calls = []
    worker = SimpleNamespace(
        events=queue.SimpleQueue(),
        cancel=threading.Event(),
        submit=lambda operation, config: calls.append((operation, config)),
    )
    app = Application(FakeWidget(), path, worker=worker)
    try:
        assert not errors
        assert not calls  # Loading a GUI must not connect to hardware.
        assert app.values["max_current_a"].get() == ""
        app.dispatch("connect")
        assert calls[-1][0] == "connect"
        assert app.busy
        assert all(
            widget.options["state"] == "disabled" for widget, role in app.buttons
        )
        worker.events.put(("state", {"connected": True, "prepared": True}))
        worker.events.put(("idle", {"operation": "connect"}))
        app.poll()
        assert app.prepared and not app.busy
        app.grid_shape = (2, 2)
        app.grid_axes = ((-1, 1), (-1, 1))
        worker.events.put(
            (
                "pixel",
                {
                    "row": 0,
                    "column": 1,
                    "current_a": -2e-9,
                    "completed": 1,
                    "total": 4,
                    "actual_x": 1,
                    "actual_y": -1,
                },
            )
        )
        app.poll()
        assert app.cells[(0, 1)] == -2e-9
        assert app.progress.options["value"] == 1
        app.close()
        assert worker.cancel.is_set()
        app.poll()
        assert calls[-1][0] == "shutdown"
        worker.events.put(("stopped", {}))
        app.poll()
        assert app.root.destroyed
    finally:
        if app.log_file is not None:
            app.log_file.close()


def test_instance_lock_released_after_error(tmp_path):
    from spectromagpythontools.gui.instance import single_instance

    with (
        single_instance(tmp_path),
        pytest.raises(RuntimeError, match="already open"),
        single_instance(tmp_path),
    ):
        pass
    with single_instance(tmp_path):
        assert (tmp_path / "logs" / "commissioning.lock").exists()


def test_ui_invalid_dut_limits_never_submit(monkeypatch, tmp_path):
    errors = fake_tk(monkeypatch)
    from spectromagpythontools.gui.commissioning import Application

    path = tmp_path / "config" / "instruments.local.toml"
    path.parent.mkdir()
    path.write_text('[pgm1se]\nserial="95XXXXXX"\n')
    calls = []
    worker = SimpleNamespace(
        events=queue.SimpleQueue(),
        cancel=threading.Event(),
        submit=lambda *args: calls.append(args),
    )
    app = Application(FakeWidget(), path, worker=worker)
    try:
        app.read_current()
        app.start_map()
        assert len(errors) == 2
        assert not calls
    finally:
        if app.log_file is not None:
            app.log_file.close()


def test_worker_cancel_active_map_then_shutdown(tmp_path):
    session, cfg, _native, transport, _events = session_fixture(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.prepare(cfg, cancel)
    worker = HardwareWorker(lambda emit: session)
    session.emit = worker._emit
    cfg.update(nx=10, ny=10)
    try:
        worker.submit("map", cfg)
        deadline = time.monotonic() + 4
        while True:
            kind, _payload = worker.events.get(timeout=4)
            if kind == "pixel":
                break
            assert time.monotonic() < deadline
        worker.cancel.set()
        wait_idle(worker)
        assert transport.v["source.output"] == 0
        footer = json.loads(Path(cfg["output_csv"]).read_text().splitlines()[-1][2:])
        assert footer["status"] == "partial"
        assert footer["pixels"] < 100
        worker.submit("shutdown", {})
        worker._thread.join(3)
        assert not worker._thread.is_alive()
    finally:
        if worker._thread.is_alive():
            worker.cancel.set()
            wait_idle(worker)
            worker.submit("shutdown", {})
            worker._thread.join(3)
