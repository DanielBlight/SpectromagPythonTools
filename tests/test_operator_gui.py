"""Offline operator workflow tests. Synthetic transports, no physical hardware."""

import json
import os
import queue
import threading
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_commissioning import FakeWidget, config, fake_tk, session_fixture

from spectromagpythontools.gui.operator_control import OperatorSession
from spectromagpythontools.gui.operator_profile import (
    motion_environment,
    operation_config,
    validate_profile,
)
from spectromagpythontools.gui.preferences import save_preferences


def profile(tmp_path):
    cfg = config(tmp_path)
    return {
        "pgm1se": cfg["pgm1se"],
        "keithley": cfg["keithley"],
        "photocurrent_map": {
            "mirror_tolerance_mrad": 0.005,
            "mirror_stable_s": 0.05,
            "mirror_timeout_s": 10,
        },
        "operator_motion": {
            "policy": "one_check",
            "delay_s": 0.08,
            "feedback_request": "combined",
            "full_check_s": 5,
        },
    }


def test_profile_overrides_hidden_values_without_mutating(tmp_path):
    p = profile(tmp_path)
    before = deepcopy(p)
    result = operation_config(
        p,
        {"bias_v": "0.5", "max_current_a": "1e-6", "mirror_tolerance_mrad": "0.1"},
        remote=False,
        serpentine=True,
    )
    assert result["mirror_tolerance_mrad"] == 0.005
    assert result["pgm1se"]["x_calibration"] == p["pgm1se"]["x_calibration"]
    assert result["max_abs_bias_v"] == 0.5
    assert "commissioning_ack" not in result
    result["pgm1se"]["serial"] = "changed"
    assert p == before


@pytest.mark.parametrize(
    "key,value",
    [
        ("delay_s", 0.01),
        ("delay_s", float("nan")),
        ("policy", "invalid"),
        ("feedback_request", "invalid"),
        ("full_check_s", 20),
    ],
)
def test_invalid_profile_timing_rejected(tmp_path, key, value):
    p = profile(tmp_path)
    p["operator_motion"][key] = value
    with pytest.raises((ValueError, TypeError)):
        validate_profile(p)


def test_missing_calibration_rejected(tmp_path):
    p = profile(tmp_path)
    del p["pgm1se"]["x_calibration"]
    with pytest.raises(ValueError, match="verified X"):
        validate_profile(p)


def test_environment_restored_on_error(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.4")
    monkeypatch.delenv("SPECTROMAG_FEEDBACK_REQUEST", raising=False)
    with (
        pytest.raises(RuntimeError),
        motion_environment({"operator_motion": {"delay_s": 0.08}}),
    ):
        assert os.environ["SPECTROMAG_MIRROR_DELAY_S"] == "0.08"
        assert os.environ["SPECTROMAG_FEEDBACK_REQUEST"] == "combined"
        raise RuntimeError("synthetic failure")
    assert os.environ["SPECTROMAG_MIRROR_DELAY_S"] == "0.4"
    assert "SPECTROMAG_FEEDBACK_REQUEST" not in os.environ


def operator_session(tmp_path):
    old, cfg, native, transport, events = session_fixture(tmp_path)
    session = OperatorSession(
        old.emit, mount_factory=old.mount_factory, smu_factory=old.smu_factory
    )
    cfg["operator_motion"] = {
        "policy": "one_check",
        "delay_s": 0.08,
        "feedback_request": "combined",
    }
    cfg["application"] = "SpectromagPythonTools operator GUI"
    cfg.pop("commissioning_ack")
    return session, cfg, native, transport, events


def test_connect_prepares_checks_smu_without_target_or_output_enable(tmp_path):
    session, cfg, native, transport, _ = operator_session(tmp_path)
    try:
        result = session.connect(cfg, threading.Event())
        assert session.prepared and session.keithley_checked
        assert "position" in result and "identity" in result
        assert any(c[0] == "prepare" for c in native.calls)
        assert not any(c[0] == "set" for c in native.calls)
        assert not any("OUTPUT_ON" in c for c in transport.log)
        assert transport.v["source.output"] == 0
    finally:
        session.disconnect({}, threading.Event())


def test_connect_failure_cleans_up_mirror(tmp_path):
    session, cfg, native, _, _ = operator_session(tmp_path)
    cfg["pgm1se"].pop("x_calibration")
    with pytest.raises(Exception, match="calibrations"):
        session.connect(cfg, threading.Event())
    assert (
        session.mount is None and not session.prepared and not session.keithley_checked
    )
    assert native.calls[-1] == ("close",)


def test_smu_failure_does_not_connect_mirror(tmp_path):
    session, cfg, native, transport, _ = operator_session(tmp_path)
    transport.model = "2636B"
    with pytest.raises(Exception, match="Expected"):
        session.connect(cfg, threading.Event())
    assert native.calls == []
    assert session.mount is None


def test_map_without_ack_and_metadata_not_fabricated(tmp_path):
    session, cfg, _, transport, _ = operator_session(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    try:
        result = session.map(cfg, cancel)
        assert result["pixels"] == 4
        metadata = json.loads(Path(cfg["output_csv"]).read_text().splitlines()[0][2:])
        assert "commissioning_ack" not in metadata["acquisition"]
        assert metadata["acquisition"]["application"].endswith("operator GUI")
        assert transport.v["source.output"] == 0
    finally:
        session.disconnect({}, cancel)


def test_cancelled_map_retains_partial_and_output_off(tmp_path):
    session, cfg, _, transport, _ = operator_session(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    session.emit = lambda kind, value: cancel.set() if kind == "pixel" else None
    try:
        with pytest.raises(Exception, match="Cancelled"):
            session.map(cfg, cancel)
        footer = json.loads(Path(cfg["output_csv"]).read_text().splitlines()[-1][2:])
        assert footer["status"] == "partial"
        assert transport.v["source.output"] == 0
    finally:
        session.disconnect({}, cancel)


def test_connect_cancelled_before_io(tmp_path):
    session, cfg, native, transport, _ = operator_session(tmp_path)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(Exception, match="cancelled"):
        session.connect(cfg, cancel)
    assert native.calls == [] and transport.log == []


def test_duplicate_connect_does_not_close_existing_session(tmp_path):
    session, cfg, native, transport, _ = operator_session(tmp_path)
    cancel = threading.Event()
    session.connect(cfg, cancel)
    before = (list(native.calls), list(transport.log))
    try:
        with pytest.raises(RuntimeError, match="Disconnect"):
            session.connect(cfg, cancel)
        assert session.prepared and session.keithley_checked
        assert before == (native.calls, transport.log)
    finally:
        session.disconnect({}, cancel)


def build_app(monkeypatch, tmp_path):
    errors = fake_tk(monkeypatch)
    from spectromagpythontools.gui.operator import Application

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "preferences"))
    path = tmp_path / "config/instruments.local.toml"
    path.parent.mkdir(exist_ok=True)
    path.write_text("""[pgm1se]
serial="95000001"
[pgm1se.x_calibration]
verified=true
raw_minus_10=-10000
raw_plus_10=10000
evidence="SYNTHETIC TEST"
[pgm1se.y_calibration]
verified=true
raw_minus_10=-10000
raw_plus_10=10000
evidence="SYNTHETIC TEST"
[keithley]
resource="USB0::TEST::INSTR"
model="2602B"
[photocurrent_map]
mirror_tolerance_mrad=0.005
mirror_stable_s=0.05
mirror_timeout_s=10
""")
    save_preferences(
        path,
        {
            "mirror_tolerance_mrad": "0.1",
            "response_wait_s": "1",
            "bias_v": "0.5",
            "max_current_a": "1e-6",
            "samples": "1",
        },
        {},
    )
    calls = []
    worker = SimpleNamespace(
        events=queue.SimpleQueue(),
        cancel=threading.Event(),
        submit=lambda *args: calls.append(args),
    )
    return Application(FakeWidget(), path, worker=worker), calls, errors


def test_operator_ui_has_no_engineering_fields_and_does_not_autoconnect(
    monkeypatch, tmp_path
):
    app, calls, errors = build_app(monkeypatch, tmp_path)
    try:
        assert not errors and not calls
        assert not {
            "serial",
            "resource",
            "x_minus",
            "mirror_tolerance_mrad",
            "response_wait_s",
        }.intersection(app.values)
        assert not hasattr(app, "ack")
        cfg = app.config()
        assert cfg["mirror_tolerance_mrad"] == 0.005
        assert cfg["samples"] == "1" and cfg["bias_v"] == 0.5
        app.dispatch("connect")
        assert calls[-1][0] == "connect"
        assert "commissioning_ack" not in calls[-1][1]
    finally:
        app.log_file.close()


def test_operator_start_map_no_confirmation_and_new_filename(monkeypatch, tmp_path):
    app, calls, errors = build_app(monkeypatch, tmp_path)
    try:
        app.prepared = True
        app.messagebox.askyesno = lambda *a: pytest.fail(
            "No acknowledgement dialog expected"
        )
        old = Path(app.values["output_csv"].get())
        old.write_text("preserve")
        app.start_map()
        assert not errors and calls[-1][0] == "map"
        assert Path(calls[-1][1]["output_csv"]) != old
        assert old.read_text() == "preserve"
    finally:
        app.log_file.close()


@pytest.mark.parametrize(
    "cmap",
    [
        "inferno",
        "magma",
        "viridis",
        "plasma",
        "cividis",
        "turbo",
        "gray",
        "coolwarm",
        "RdBu_r",
    ],
)
def test_colour_options_render(cmap):
    from matplotlib.backends.backend_agg import FigureCanvasAgg

    from spectromagpythontools.gui.map_plot import MapFigure

    plot = MapFigure()
    plot.update((0, 1), (0, 1), {(0, 0): 1e-9, (1, 1): 2e-9}, cmap=cmap)
    assert plot.image.get_cmap().name == cmap
    FigureCanvasAgg(plot.figure).draw()


def test_disconnect_not_blocked_by_invalid_measurement_entry(monkeypatch, tmp_path):
    app, calls, errors = build_app(monkeypatch, tmp_path)
    try:
        app.connected = True
        app.values["bias_v"].set("not a number")
        app.dispatch("disconnect")
        assert calls[-1][0] == "disconnect" and not errors
    finally:
        app.log_file.close()
