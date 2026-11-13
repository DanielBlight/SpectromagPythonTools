import csv
import io
import json
import threading

import pytest

from spectromagpythontools.devices.keithley2600 import (
    AcquisitionCancelled,
    InstrumentError,
    KeithleyPhotocurrent,
    Settings,
)
from spectromagpythontools.routines.photocurrent_map import read_point, scan_map


class Fake:
    def __init__(self):
        self.log = []
        self.v = {
            "source.func": 1,
            "source.levelv": 0,
            "source.limiti": 1e-06,
            "measure.nplc": 1,
            "source.output": 0,
        }
        self.response = "2e-9 0 false 1 1e-7"
        self.error = 0
        self.model = "2602B"
        self.sweep = 0
        self.failure = None
        self.bad = False

    def write(self, c):
        self.log.append(c)
        if c.startswith(("smua.", "smub.")) and " = " in c:
            k, v = c.split(" = ")
            try:
                v = float(v)
            except ValueError:
                v = {"OUTPUT_ON": 1, "OUTPUT_HIGH_Z": 0, "OUTPUT_DCVOLTS": 1}.get(
                    v.split(".")[-1], v
                )
            self.v[k.split(".", 1)[1]] = v

    def query(self, c):
        self.log.append(c)
        if c == "*IDN?":
            return "Keithley Instruments Inc.,MODEL " + self.model + ",123,1"
        if c == "print(errorqueue.count)":
            return str(self.error)
        if c == "print(status.operation.sweeping.condition)":
            return str(self.sweep)
        if c == "print(localnode.linefreq)":
            return "50"
        if c.startswith("local function spb_acquire()"):
            import re

            if self.failure:
                raise self.failure
            size = int(re.search(r"for k=1,(\d+) do", c).group(1))
            fields = self.response.split()
            if (
                len(fields) != 5
                or fields[2] != "false"
                or fields[3] != "1"
                or self.error
                or self.v["source.output"] != 1
            ):
                self.v["source.output"] = 0
                return "SPB1 ERR batch_failed"
            try:
                valid = all(abs(float(fields[i])) < 9e37 for i in (0, 1, 4))
                valid = valid and float(fields[4]) > 0
            except ValueError:
                valid = False
            if not valid:
                self.v["source.output"] = 0
                return "SPB1 ERR batch_failed"
            return f"SPB1 OK {size} " + " ".join(
                [fields[0], fields[1], fields[4]] * size
            )
        if c.startswith("local i,v"):
            if self.failure:
                raise self.failure
            return self.response
        keys = [k.strip().split(".", 1)[1] for k in c[6:-1].split(",")]
        values = [self.v[k] for k in keys]
        if self.bad and len(values) == 5:
            values[2] = 0.1
        return "\t".join(map(str, values))


def make(configure=True, **kw):
    f = Fake()
    s = KeithleyPhotocurrent(
        "FAKE",
        expected_model="2602B",
        max_abs_bias_v=1,
        max_current_a=1e-05,
        session=f,
        **kw,
    )
    if configure:
        s.connect()
        s.configure(Settings(0, 1e-06))
    return (s, f)


@pytest.mark.parametrize("v", [float("nan"), float("inf"), True])
def test_finite(v):
    with pytest.raises(ValueError):
        Settings(v, 1e-06)


@pytest.mark.parametrize(
    "kw", [{"nplc": 0}, {"nplc": 26}, {"current_range_a": -1}, {"remote_sense": 1}]
)
def test_settings(kw):
    with pytest.raises(ValueError):
        Settings(0, 1e-06, **kw)


def test_lazy():
    s, f = make(False)
    assert not f.log
    with pytest.raises(InstrumentError):
        s.enable_output()
    assert not f.log


def test_mismatch():
    s, f = make(False)
    f.model = "2636B"
    with pytest.raises(InstrumentError):
        s.connect()
    assert f.log == ["*IDN?"]


def test_sweep():
    s, f = make(False)
    f.sweep = 2
    with pytest.raises(InstrumentError):
        s.connect()
    assert not any(" = " in c for c in f.log)


def test_error_preserved():
    s, f = make(False)
    f.error = 2
    with pytest.raises(InstrumentError):
        s.connect()
    assert not any("clear" in c for c in f.log)


def test_no_implicit_enable():
    s, f = make()
    assert not any("OUTPUT_ON" in c for c in f.log)
    assert not any("reset()" in c or "smub" in c for c in f.log)
    s.enable_output()
    assert f.log.index("smua.source.limiti = 1e-06") < f.log.index(
        "smua.source.output = smua.OUTPUT_ON"
    )
    s.close()
    assert f.v["source.output"] == 0


def test_readback():
    s, f = make()
    f.bad = True
    with pytest.raises(InstrumentError):
        s.enable_output()
    assert not any("OUTPUT_ON" in c for c in f.log)


@pytest.mark.parametrize(
    "cfg", [Settings(2, 1e-06), Settings(0, 0.0001), Settings(0, 1e-10)]
)
def test_limits(cfg):
    s, f = make()
    before = list(f.log)
    with pytest.raises(ValueError):
        s.configure(cfg)
    assert before == f.log


def test_read():
    s, f = make()
    with s.output_enabled():
        p = read_point(s, samples=3)
    assert len(p.readings) == 3
    assert p.mean_current_a == pytest.approx(2e-09)
    assert p.sample_std_current_a == 0
    assert f.v["source.output"] == 0


@pytest.mark.parametrize(
    "r",
    [
        "9.91e37 0 false 1 1e-7",
        "nan 0 false 1 1e-7",
        "2e-9 0 true 1 1e-7",
        "2e-9 0 false 0 1e-7",
        "bad",
    ],
)
def test_bad_read(r):
    s, f = make()
    s.enable_output()
    f.response = r
    with pytest.raises((InstrumentError, ValueError)):
        read_point(s)
    assert f.v["source.output"] == 0
    with pytest.raises(InstrumentError):
        s.enable_output()


def test_timeout():
    s, f = make()
    s.enable_output()
    f.failure = TimeoutError("primary")
    with pytest.raises(TimeoutError, match="primary") as e:
        read_point(s)
    assert "NOT confirmed" in " ".join(e.value.__notes__)
    assert "OUTPUT_HIGH_Z" in f.log[-1]


def test_cancel():
    s, f = make()
    s.enable_output()
    event = threading.Event()
    event.set()
    with pytest.raises(AcquisitionCancelled):
        read_point(s, cancel=event)
    assert f.v["source.output"] == 0


def test_map(tmp_path):
    s, f = make()
    positions = []

    def move(x, y):
        positions.append((x, y))
        return (x + 0.01, y)

    p = tmp_path / "map.csv"
    assert (
        scan_map(s, xs=[0, 1], ys=[0, 1], move_and_wait=move, output_csv=p, settle_s=0)
        == 4
    )
    assert positions == [(0, 0), (1, 0), (1, 1), (0, 1)]
    lines = p.read_text().splitlines()
    assert json.loads(lines[-1][2:])["status"] == "complete"
    rows = list(
        csv.DictReader(
            io.StringIO("\n".join(line for line in lines if not line.startswith("#")))
        )
    )
    assert [int(r["column"]) for r in rows] == [0, 1, 1, 0]
    assert float(rows[0]["actual_x"]) == 0.01
    assert f.v["source.output"] == 0


def test_collision(tmp_path):
    s, f = make()
    p = tmp_path / "map.csv"
    p.write_text("existing")
    with pytest.raises(FileExistsError):
        scan_map(s, xs=[0], ys=[0], move_and_wait=lambda x, y: (x, y), output_csv=p)
    assert p.read_text() == "existing"
    assert not any("OUTPUT_ON" in c for c in f.log)


def test_partial(tmp_path):
    s, f = make()
    p = tmp_path / "map.csv"

    def move(x, y):
        if x:
            raise RuntimeError("motion failed")
        return (x, y)

    with pytest.raises(RuntimeError, match="motion failed"):
        scan_map(s, xs=[0, 1], ys=[0], move_and_wait=move, output_csv=p, settle_s=0)
    footer = json.loads(p.read_text().splitlines()[-1][2:])
    assert footer["status"] == "partial" and footer["pixels"] == 1
    assert f.v["source.output"] == 0


def test_b():
    _s, f = make(channel="b")
    assert any("smub.measure" in c for c in f.log)
    assert not any("smua" in c for c in f.log)


def test_closed_wrapper_cannot_reconnect():
    s, f = make()
    s.close()
    before = list(f.log)
    with pytest.raises(InstrumentError):
        s.connect()
    assert f.log == before


def test_cancel_before_map_enable(tmp_path):
    s, f = make()
    event = threading.Event()
    event.set()
    with pytest.raises(AcquisitionCancelled):
        scan_map(
            s,
            xs=[0],
            ys=[0],
            move_and_wait=lambda x, y: (x, y),
            output_csv=tmp_path / "map.csv",
            cancel=event,
        )
    assert not any("OUTPUT_ON" in c for c in f.log)


def test_single_device_read():
    smu, session = make()
    with smu.output_enabled():
        reading = smu.read()
    assert reading.current_a == pytest.approx(2e-9)
    assert reading.voltage_v == 0
    assert session.v["source.output"] == 0


def test_device_has_no_mapping_api():
    from spectromagpythontools.devices import keithley2600

    assert not hasattr(keithley2600, "scan_map")
    assert not hasattr(keithley2600, "Point")
    assert not hasattr(KeithleyPhotocurrent, "read_point")


def test_configuration_accepts_2636b_float_rounding():
    smu, session = make()
    session.v["source.limiti"] = 1.0000000475e-3

    smu.settings = Settings(0.5, 1e-3, nplc=1.0)
    session.v["source.func"] = 1
    session.v["source.levelv"] = 0.5
    session.v["measure.nplc"] = 1.0
    session.v["source.output"] = 0

    smu._verify_settings()
