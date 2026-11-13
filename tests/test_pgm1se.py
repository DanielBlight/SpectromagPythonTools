"""Synthetic backend tests only. No vendor DLLs, USB, motion or laser access."""

import ctypes
import threading
import time

import pytest

from spectromagpythontools.devices.pgm1se import (
    PGM1SE,
    AxisCalibration,
    GimbalError,
    KinesisPPC102,
    MoveCancelled,
    RawState,
    _IOSettings,
)
from spectromagpythontools.routines.gimbal_positioning import gimbal_mover


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.positions = {1: 0, 2: 0}
        self.bits = 2147484929
        self.mode = 2
        self.source = 0
        self.identity = "PPC102"
        self.freeze = False
        self.fail_channel = None
        self.read_error = False
        self.close_error = False

    def open(self, serial):
        self.calls.append(("open", serial))
        return self.identity

    def read(self, channel):
        self.calls.append(("read", channel))
        if self.read_error:
            raise OSError("read failed")
        return RawState(
            channel,
            self.positions[channel],
            self.bits,
            self.mode,
            self.source,
            1,
            time.time(),
        )

    def prepare(self, channel):
        self.calls.append(("prepare", channel))
        self.mode, self.source = (2, 0)

    def set_position(self, channel, raw):
        self.calls.append(("set", channel, raw))
        if channel == self.fail_channel:
            raise OSError("set failed")
        if not self.freeze:
            self.positions[channel] = raw

    def close(self):
        self.calls.append(("close",))
        if self.close_error:
            raise OSError("close failed")


def make(**kwargs):
    backend = FakeBackend()
    calibration = AxisCalibration(-10000, 10000, "synthetic test fixture")
    mount = PGM1SE(
        "95000001",
        backend=backend,
        x_calibration=calibration,
        y_calibration=calibration,
        **kwargs,
    )
    return (mount, backend)


def ready(**kwargs):
    mount, backend = make(**kwargs)
    mount.connect()
    mount.prepare_closed_loop()
    return (mount, backend)


def test_no_io_on_construction():
    mount, backend = make()
    assert not backend.calls
    assert mount.serial == "95000001"


@pytest.mark.parametrize("serial", ["", "44000001", "95abc123", None])
def test_serial(serial):
    with pytest.raises(ValueError):
        PGM1SE(serial)


@pytest.mark.parametrize("limit", [0, -1, 10.1, float("nan"), float("inf")])
def test_limits(limit):
    with pytest.raises(ValueError):
        make(max_abs_mrad=limit)


def test_axis_mapping():
    with pytest.raises(ValueError):
        make(x_channel=1, y_channel=1)


@pytest.mark.parametrize("ends", [(0, 0), (-32768, 32767), (0, 40000), (False, 1)])
def test_calibration_rejects(ends):
    with pytest.raises(ValueError):
        AxisCalibration(*ends, "test")


def test_calibration_conversion():
    cal = AxisCalibration(-10000, 10000, "synthetic")
    for angle in (-10, -1, 0, 1, 10):
        assert cal.from_raw(cal.to_raw(angle)) == pytest.approx(angle)
    reversed_cal = AxisCalibration(10000, -10000, "synthetic")
    assert reversed_cal.to_raw(2) == -2000
    with pytest.raises(ValueError):
        cal.to_raw(10.001)


def test_inspection_does_not_prepare_or_move():
    mount, backend = make()
    with mount:
        assert len(mount.raw_state()) == 2
    assert not any(call[0] in {"prepare", "set"} for call in backend.calls)


def test_identity_mismatch_closes():
    mount, backend = make()
    backend.identity = "PPC001"
    with pytest.raises(GimbalError):
        mount.connect()
    assert backend.calls[-1] == ("close",)


def test_calibration_required_before_prepare():
    backend = FakeBackend()
    with (
        PGM1SE("95000001", backend=backend) as mount,
        pytest.raises(GimbalError, match="calibrations"),
    ):
        mount.prepare_closed_loop()
    assert not any(call[0] == "prepare" for call in backend.calls)


def test_explicit_prepare_required():
    mount, backend = make()
    mount.connect()
    with pytest.raises(GimbalError):
        mount.move_and_wait(0, 0)
    assert not any(call[0] == "set" for call in backend.calls)


def test_success_and_readback():
    mount, backend = ready()
    result = mount.move_and_wait(1, -2, stable_s=0.05)
    assert result == pytest.approx((1, -2))
    assert ("set", 1, 1000) in backend.calls
    assert ("set", 2, -2000) in backend.calls


def test_both_targets_validated_before_motion():
    mount, backend = ready()
    with pytest.raises(ValueError):
        mount.move_and_wait(1, 11)
    assert not any(call[0] == "set" for call in backend.calls)


@pytest.mark.parametrize("bit", [2048, 4096, 8192, 1073741824])
def test_fault_prevents_motion(bit):
    mount, backend = ready()
    backend.bits |= bit
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, 1)
    assert not any(call[0] == "set" for call in backend.calls)


@pytest.mark.parametrize("bit", [1, 1024, 2147483648])
def test_missing_status(bit):
    mount, backend = ready()
    backend.bits &= ~bit
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, 1)
    assert not any(call[0] == "set" for call in backend.calls)


def test_external_source_refused():
    mount, backend = ready()
    backend.source = 1
    with pytest.raises(GimbalError):
        mount.move_and_wait(1, 1)


def test_timeout_faults_without_parking():
    mount, backend = ready()
    backend.freeze = True
    with pytest.raises(TimeoutError):
        mount.move_and_wait(1, 1, stable_s=0.05, timeout_s=0.1)
    assert [call for call in backend.calls if call[0] == "set"] == [
        ("set", 1, 1000),
        ("set", 2, 1000),
    ]
    with pytest.raises(GimbalError):
        mount.move_and_wait(0, 0)


def test_cancel_before_commands():
    mount, backend = ready()
    event = threading.Event()
    event.set()
    with pytest.raises(MoveCancelled):
        mount.move_and_wait(1, 1, cancel=event)
    assert not any(call[0] == "set" for call in backend.calls)


def test_partial_axis_failure_no_rollback():
    mount, backend = ready()
    backend.fail_channel = 2
    with pytest.raises(OSError, match="set failed"):
        mount.move_and_wait(1, 2)
    assert backend.positions[1] == 1000
    assert len([call for call in backend.calls if call[0] == "set"]) == 2


def test_close_does_not_park():
    mount, backend = ready()
    mount.close()
    assert backend.calls[-1] == ("close",)
    assert not any(call[0] == "set" for call in backend.calls)
    with pytest.raises(GimbalError):
        mount.connect()


def test_primary_error_survives_close_failure():
    mount, backend = make()
    with pytest.raises(RuntimeError, match="primary") as caught, mount:
        backend.close_error = True
        raise RuntimeError("primary")
    assert "close failed" in " ".join(caught.value.__notes__)


def test_callback_adapter():
    mount, backend = ready(x_channel=2, y_channel=1)
    mover = gimbal_mover(mount, stable_s=0.05)
    assert mover(2, -1) == pytest.approx((2, -1))
    assert backend.positions == {1: -1000, 2: 2000}


def test_abi_layout_and_native_error():
    assert ctypes.sizeof(_IOSettings) == 12
    assert _IOSettings.feedback_polarity.offset == 10
    with pytest.raises(GimbalError, match="17"):
        KinesisPPC102._ok(17, "test")


def test_native_prepare_preserves_other_io_fields():
    from types import SimpleNamespace

    backend = KinesisPPC102()
    backend._serial = b"95000001"
    recorded = []

    def get_io(serial, channel, pointer):
        for index, (name, _) in enumerate(_IOSettings._fields_):
            setattr(pointer._obj, name, index + 1)
        return 0

    def set_io(serial, channel, pointer):
        recorded.append(
            tuple((getattr(pointer._obj, name) for name, _ in _IOSettings._fields_))
        )
        return 0

    backend._dll = SimpleNamespace(
        PPC2_GetIOSettings=get_io,
        PPC2_SetIOSettings=set_io,
        PPC2_SetPositionControlMode=lambda serial, channel, mode: (
            recorded.append(("mode", channel, mode)) or 0
        ),
        PPC2_EnableChannel=lambda serial, channel: (
            recorded.append(("enable", channel)) or 0
        ),
    )
    backend.prepare(2)
    assert recorded == [(0, 2, 3, 4, 5, 6), ("mode", 2, 2), ("enable", 2)]


def test_native_set_error_is_not_retried():
    from types import SimpleNamespace

    backend = KinesisPPC102()
    backend._serial = b"95000001"
    calls = []

    def set_position(serial, channel, raw):
        calls.append((channel, raw))
        return 7

    backend._dll = SimpleNamespace(PPC2_SetPosition=set_position)
    with pytest.raises(GimbalError, match="7"):
        backend.set_position(1, 123)
    assert calls == [(1, 123)]


def test_mount_uses_batched_backend_read():
    class BatchedBackend(FakeBackend):
        def read_channels(self, channels):
            channels = tuple(channels)
            self.calls.append(("read_channels", channels))

            return tuple(
                RawState(
                    channel,
                    self.positions[channel],
                    self.bits,
                    self.mode,
                    self.source,
                    1,
                    0.0,
                )
                for channel in channels
            )

    backend = BatchedBackend()
    mount = PGM1SE(
        "95000001",
        backend=backend,
    )

    mount.connect()
    states = mount.raw_state()
    mount.close()

    assert [state.channel for state in states] == [1, 2]
    assert ("read_channels", (1, 2)) in backend.calls
    assert not any(call[0] == "read" for call in backend.calls)
