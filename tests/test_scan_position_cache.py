"""Offline tests for scan-only accepted-position reuse; no hardware access."""

import threading
from types import SimpleNamespace

import pytest

from spectromagpythontools.devices.pgm1se import PGM1SE, GimbalError, MoveCancelled
from spectromagpythontools.routines.scan_position_cache import (
    accept_position,
    initial_position,
    invalidate,
    scan_position_reuse,
)


class Calibration:
    resolution_mrad = 1 / 3276

    def to_raw(self, value):
        return round(value * 3276)


class Mount:
    _one_check_move = PGM1SE._one_check_move

    def __init__(self):
        self._lock = threading.RLock()
        self._backend = self
        self._prepared = True
        self._faulted = False
        self.x_channel, self.y_channel = (1, 2)
        self.x_calibration = self.y_calibration = Calibration()
        self.max_abs_mrad = 10
        self.response_wait_s = 0.05
        self._settings_checked_at = 1
        self.x = self.y = 0
        self.full = self.polls = self.fallbacks = 0
        self.commands = []
        self.miss = self.fail = False

    def _require(self):
        if self._faulted:
            raise GimbalError("synthetic fault")

    def position(self):
        self.full += 1
        return SimpleNamespace(x_mrad=self.x, y_mrad=self.y)

    def _motion_position(self):
        self.polls += 1
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
        self.fallbacks += 1
        self.x, self.y = (x, y)
        return (x, y)

    def move(self, x, y, cancel=None):
        return self._one_check_move(
            x, y, tolerance_mrad=0.001, stable_s=0.05, timeout_s=2, cancel=cancel
        )


@pytest.fixture
def mount(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.05")
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "5")
    return Mount()


def test_manual_moves_keep_precommand_read(mount):
    mount.move(0.005, 0)
    first = mount.polls
    mount.move(0.01, 0)
    assert mount.polls - first == 2
    assert not hasattr(mount, "_scan_position_reuse_state")


def test_scan_reuses_only_precommand_position(mount):
    with scan_position_reuse(mount) as state:
        mount.move(0.005, 0)
        first = mount.polls
        mount.move(0.01, 0)
        assert mount.polls - first == 1
        assert state["reused"] == 1 and state["accepted"] == 2
        assert mount.full == 1
    assert not hasattr(mount, "_scan_position_reuse_state")


def test_new_scan_starts_without_accepted_cache(mount):
    with scan_position_reuse(mount):
        mount.move(0.005, 0)
    first = mount.polls
    with scan_position_reuse(mount) as state:
        mount.move(0.01, 0)
        assert state["reused"] == 0
    assert mount.polls - first == 2


def test_fallback_discards_cache(mount):
    with scan_position_reuse(mount) as state:
        mount.move(0.005, 0)
        mount.miss = True
        mount.move(0.01, 0)
        assert mount.fallbacks == 1
        assert state["position"] is None


def test_fault_discards_cache_and_scope(mount):
    with pytest.raises(GimbalError), scan_position_reuse(mount) as state:
        mount.move(0.005, 0)
        mount.fail = True
        mount.move(0.01, 0)
    assert state["position"] is None
    assert not mount._prepared and mount._faulted
    assert not hasattr(mount, "_scan_position_reuse_state")


def test_cancel_discards_cache(mount):
    event = threading.Event()
    with pytest.raises(MoveCancelled), scan_position_reuse(mount) as state:
        mount.move(0.005, 0)
        event.set()
        mount.move(0.01, 0, cancel=event)
    assert state["position"] is None
    assert not hasattr(mount, "_scan_position_reuse_state")


def test_periodic_full_check_still_runs(mount):
    with scan_position_reuse(mount):
        mount.move(0.005, 0)
        mount._fast_checked_at -= 6
        mount.move(0.01, 0)
        assert mount.full == 2


@pytest.mark.parametrize(
    "change", ["calibration", "settings_clock", "response_wait", "channel"]
)
def test_changed_signature_invalidates(mount, change):
    with scan_position_reuse(mount) as state:
        position = mount.position()
        accept_position(mount, position)
        if change == "calibration":
            mount.x_calibration = Calibration()
        elif change == "settings_clock":
            mount._settings_checked_at += 1
        elif change == "response_wait":
            mount.response_wait_s = 0.1
        else:
            mount.x_channel = 2
        initial_position(mount)
        assert mount.polls == 1
        assert state["position"] is None


def test_other_thread_cannot_use_cache(mount):
    with scan_position_reuse(mount) as state:
        accept_position(mount, mount.position())
        thread = threading.Thread(target=lambda: initial_position(mount))
        thread.start()
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert state["reused"] == 0 and mount.polls == 1


def test_nested_scope_rejected(mount):
    with (
        scan_position_reuse(mount),
        pytest.raises(RuntimeError, match="Nested"),
        scan_position_reuse(mount),
    ):
        pass


def test_custom_mount_unchanged():
    with scan_position_reuse(SimpleNamespace()) as state:
        assert state is None


def test_explicit_invalidation(mount):
    with scan_position_reuse(mount) as state:
        accept_position(mount, mount.position())
        invalidate(mount)
        assert state["position"] is None
        initial_position(mount)
        assert mount.polls == 1


@pytest.mark.parametrize("name", ["connect", "close", "prepare_closed_loop"])
def test_lifecycle_invalidates_cache(name):
    import inspect

    assert "invalidate(self)" in inspect.getsource(getattr(PGM1SE, name))


def test_acquisition_error_clears_scan_cache(mount):
    with (
        pytest.raises(RuntimeError, match="synthetic acquisition"),
        scan_position_reuse(mount) as state,
    ):
        mount.move(0.005, 0)
        raise RuntimeError("synthetic acquisition error")
    assert state["position"] is None
    assert not hasattr(mount, "_scan_position_reuse_state")


def test_opt_out_keeps_precommand_read(mount, monkeypatch):
    monkeypatch.setenv("SPECTROMAG_SCAN_POSITION_REUSE", "0")
    with scan_position_reuse(mount) as state:
        assert state is None
        mount.move(0.005, 0)
        first = mount.polls
        mount.move(0.01, 0)
        assert mount.polls - first == 2
