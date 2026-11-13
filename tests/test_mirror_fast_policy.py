"""Synthetic fast-policy regressions; no hardware access."""

import threading
from types import SimpleNamespace

import pytest

from spectromagpythontools.devices.pgm1se import (
    PGM1SE,
    GimbalError,
    MoveCancelled,
)


class Calibration:
    resolution_mrad = 1 / 3276

    def to_raw(self, angle):
        return round(angle * 3276)


class Fixture:
    move_and_wait = PGM1SE.move_and_wait
    _one_check_move = PGM1SE._one_check_move

    def __init__(self):
        self._lock = threading.RLock()
        self._prepared = True
        self._faulted = False
        self.max_abs_mrad = 10
        self.x_channel, self.y_channel = 1, 2
        self.x_calibration = self.y_calibration = Calibration()
        self.x = self.y = 0
        self.full = self.fast = self.fallback = 0
        self.commands = []
        self.miss = False
        self.fail = False
        self._backend = self

    def _require(self):
        if self._faulted:
            raise GimbalError("faulted")

    def position(self):
        self.full += 1
        return SimpleNamespace(x_mrad=self.x, y_mrad=self.y)

    def _motion_position(self):
        self.fast += 1
        if self.fail:
            raise GimbalError("synthetic live fault")
        return SimpleNamespace(x_mrad=0 if self.miss else self.x, y_mrad=self.y)

    def set_position(self, channel, raw):
        self.commands.append((channel, raw))
        if channel == 1:
            self.x = raw / 3276
        else:
            self.y = raw / 3276

    def _verified_move_and_wait(self, x, y, **kwargs):
        self.fallback += 1
        self.x, self.y = x, y
        return x, y


@pytest.fixture
def fast_env(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.05")
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")
    return monkeypatch


def move(mount, **kwargs):
    return mount.move_and_wait(
        0.005, 0, tolerance_mrad=0.001, stable_s=0.05, timeout_s=2, **kwargs
    )


def test_default_policy_retains_verified(monkeypatch):
    monkeypatch.delenv("SPECTROMAG_MIRROR_POLICY", raising=False)
    mount = Fixture()
    move(mount)
    assert mount.fallback == 1 and mount.full == 0


def test_one_check_only_changed_axis_and_periodic_full(fast_env):
    mount = Fixture()
    assert move(mount) == pytest.approx((16 / 3276, 0))
    assert mount.commands == [(1, 16)]
    assert mount.full == 1 and mount.fallback == 0
    move(mount)
    assert mount.full == 1 and mount.fast == 3
    assert mount.commands == [(1, 16)]
    mount._fast_checked_at -= 3
    move(mount)
    assert mount.full == 2


def test_outside_tolerance_falls_back(fast_env):
    mount = Fixture()
    mount.miss = True
    assert move(mount) == (0.005, 0)
    assert mount.fallback == 1
    assert mount._last_fast_move["fallback"]


def test_live_fault_stops_and_faults(fast_env):
    mount = Fixture()
    mount.fail = True
    with pytest.raises(GimbalError):
        move(mount)
    assert mount._faulted and not mount._prepared


def test_cancel_before_command(fast_env):
    mount = Fixture()
    event = threading.Event()
    event.set()
    with pytest.raises(MoveCancelled):
        move(mount, cancel=event)
    assert not mount.commands


def test_cancel_during_delay(fast_env):
    mount = Fixture()

    class Cancel:
        def is_set(self):
            return False

        def wait(self, delay):
            return True

    with pytest.raises(MoveCancelled):
        move(mount, cancel=Cancel())
    assert mount._faulted and not mount._prepared


@pytest.mark.parametrize(
    "key,value",
    [
        ("SPECTROMAG_MIRROR_DELAY_S", "0"),
        ("SPECTROMAG_MIRROR_DELAY_S", "nan"),
        ("SPECTROMAG_MIRROR_FULL_CHECK_S", "0"),
    ],
)
def test_bad_timing_no_commands(fast_env, key, value):
    fast_env.setenv(key, value)
    mount = Fixture()
    with pytest.raises(ValueError):
        move(mount)
    assert not mount.commands


def test_bad_target_no_commands(fast_env):
    mount = Fixture()
    with pytest.raises(ValueError):
        mount.move_and_wait(11, 0)
    assert not mount.commands
