"""Offline preferences and headless GUI tests; no instrument access."""

import queue
import sys
import threading
from types import ModuleType, SimpleNamespace

import pytest

from spectromagpythontools.configuration import measurement
from spectromagpythontools.gui import preferences


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "user-state"))


def test_roundtrip_and_exclusions(tmp_path):
    config = tmp_path / "config.toml"
    path = preferences.save_preferences(
        config,
        {
            "bias_v": "-0.5",
            "max_current_a": "1e-6",
            "samples": "3",
            "output_csv": "never.csv",
            "serial": "95000001",
            "x_minus": "-32760",
            "response_wait_s": "0.1",
        },
        {"remote_sense": True, "serpentine": False, "commissioning_ack": True},
    )
    data = preferences.load_preferences(config)
    assert data["values"] == {
        "bias_v": "-0.5",
        "max_current_a": "1e-6",
        "samples": "3",
        "response_wait_s": "0.1",
    }
    assert data["flags"] == {"remote_sense": True, "serpentine": False}
    assert not list(path.parent.glob("*.tmp"))


def test_config_scoped_paths(tmp_path):
    assert preferences.state_path(tmp_path / "a.toml") != preferences.state_path(
        tmp_path / "b.toml"
    )


@pytest.mark.parametrize(
    "text",
    ["broken", "[]", '{"schema":2}', '{"schema":true}', '{"schema":1,"values":[]}'],
)
def test_corrupt_preferences_rejected(tmp_path, text):
    config = tmp_path / "a.toml"
    path = preferences.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text(text)
    with pytest.raises((ValueError, TypeError)):
        preferences.load_preferences(config)


def test_oversized_preferences_rejected(tmp_path):
    config = tmp_path / "a.toml"
    path = preferences.state_path(config)
    path.parent.mkdir(parents=True)
    path.write_text(" " * 131073)
    with pytest.raises(ValueError):
        preferences.load_preferences(config)


def test_failed_replace_preserves_previous_file(monkeypatch, tmp_path):
    config = tmp_path / "a.toml"
    preferences.save_preferences(config, {"bias_v": "0"}, {})

    def fail(*args):
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(preferences.os, "replace", fail)
    with pytest.raises(OSError):
        preferences.save_preferences(config, {"bias_v": "1"}, {})
    assert preferences.load_preferences(config)["values"]["bias_v"] == "0"
    assert not list(preferences.state_path(config).parent.glob("*.tmp"))


class Variable:
    def __init__(self, value=None, **kwargs):
        self.value = value
        self.callbacks = []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.callbacks:
            callback()

    def trace_add(self, mode, callback):
        self.callbacks.append(callback)
        return str(len(self.callbacks))


class Widget:
    def __init__(self, *args, **kwargs):
        self.options = dict(kwargs)
        self.pending = {}
        self.next_id = 0
        self.destroyed = False

    def configure(self, **kwargs):
        self.options.update(kwargs)

    def index(self, *args):
        return "1.0"

    def winfo_width(self):
        return 450

    def winfo_height(self):
        return 420

    def after(self, ms, callback):
        self.next_id += 1
        key = str(self.next_id)
        self.pending[key] = (ms, callback)
        return key

    def after_cancel(self, key):
        self.pending.pop(key, None)

    def destroy(self):
        self.destroyed = True

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def make_app(monkeypatch, tmp_path, mapping=""):
    from spectromagpythontools.gui import map_plot

    monkeypatch.setattr(
        map_plot, "MapPlot", lambda parent: SimpleNamespace(update=lambda *args: None)
    )
    tk = ModuleType("tkinter")
    for name in ("Frame", "Text", "Canvas", "Tk"):
        setattr(tk, name, Widget)
    tk.StringVar = tk.BooleanVar = Variable
    tk.ttk = SimpleNamespace(
        **{
            name: Widget
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
    tk.filedialog = SimpleNamespace(
        askopenfilename=lambda **kw: "", asksaveasfilename=lambda **kw: ""
    )
    tk.messagebox = SimpleNamespace(
        showerror=lambda *args: errors.append(args), askyesno=lambda *args: True
    )
    monkeypatch.setitem(sys.modules, "tkinter", tk)
    from spectromagpythontools.gui.commissioning import Application

    config = tmp_path / "config" / "instruments.local.toml"
    config.parent.mkdir(exist_ok=True)
    if not config.exists():
        config.write_text('[pgm1se]\nserial="95XXXXXX"\n' + mapping)
    calls = []
    worker = SimpleNamespace(
        events=queue.SimpleQueue(),
        cancel=threading.Event(),
        submit=lambda *args: calls.append(args),
    )
    app = Application(Widget(), config, worker=worker)
    return app, calls, errors


def close_log(app):
    if app.log_file is not None:
        app.log_file.close()


def test_gui_only_bias_and_max_current_controls(monkeypatch, tmp_path):
    app, calls, errors = make_app(monkeypatch, tmp_path)
    try:
        assert "max_abs_bias_v" not in app.values
        assert "current_limit_a" not in app.values
        app.values["bias_v"].set("-0.5")
        app.values["max_current_a"].set("1e-6")
        config = app.config()
        max_v, max_i, settings = measurement(config)
        assert max_v == 0.5 and max_i == 1e-6
        assert settings.bias_v == -0.5 and settings.current_limit_a == 1e-6
        assert not calls and not errors
    finally:
        close_log(app)


def test_gui_reopens_without_output_or_ack_restore(monkeypatch, tmp_path):
    app, calls, errors = make_app(monkeypatch, tmp_path)
    original_csv = app.values["output_csv"].get()
    app.values["bias_v"].set("0.5")
    app.values["max_current_a"].set("1e-6")
    app.values["nplc"].set("3")
    app.values["samples"].set("1")
    app.remote.set(True)
    app.serpentine.set(False)
    app.ack.set(True)
    app._save_preferences()
    close_log(app)
    again, calls2, errors2 = make_app(monkeypatch, tmp_path)
    try:
        assert again.values["bias_v"].get() == "0.5"
        assert again.values["max_current_a"].get() == "1e-6"
        assert (
            again.values["nplc"].get() == "3" and again.values["samples"].get() == "1"
        )
        assert again.remote.get() and not again.serpentine.get()
        assert not again.ack.get() and not again.connected and not again.prepared
        assert again.values["output_csv"].get() != original_csv
        assert not calls and not calls2 and not errors and not errors2
    finally:
        close_log(again)


def test_legacy_toml_uses_lower_current(monkeypatch, tmp_path):
    app, _, errors = make_app(
        monkeypatch,
        tmp_path,
        "[photocurrent_map]\nmax_current_a=0.001\ncurrent_limit_a=0.000001\n",
    )
    try:
        assert float(app.values["max_current_a"].get()) == 1e-6
        assert not errors
    finally:
        close_log(app)


def test_corrupt_state_does_not_prevent_startup(monkeypatch, tmp_path):
    config = tmp_path / "config" / "instruments.local.toml"
    state = preferences.state_path(config)
    state.parent.mkdir(parents=True)
    state.write_text("broken")
    app, calls, errors = make_app(monkeypatch, tmp_path)
    try:
        assert not calls and not errors
        assert app.values["max_current_a"].get() == ""
    finally:
        close_log(app)


def test_debounce_and_close_save(monkeypatch, tmp_path):
    app, calls, errors = make_app(monkeypatch, tmp_path)
    try:
        app.values["bias_v"].set("0.25")
        first = app._preferences_after
        app.values["bias_v"].set("0.5")
        assert first not in app.root.pending
        assert app._preferences_after in app.root.pending
        app.close()
        assert preferences.load_preferences(app.path)["values"]["bias_v"] == "0.5"
        assert app._preferences_after is None
        assert not calls and not errors
    finally:
        close_log(app)


@pytest.mark.parametrize(
    "bias,current", [("nan", "1e-6"), ("21", "1e-6"), ("0.5", "0"), ("0.5", "0.2")]
)
def test_invalid_electrical_settings_do_not_submit(
    monkeypatch, tmp_path, bias, current
):
    app, calls, errors = make_app(monkeypatch, tmp_path)
    try:
        app.values["bias_v"].set(bias)
        app.values["max_current_a"].set(current)
        app.read_current()
        assert errors and not calls
    finally:
        close_log(app)
