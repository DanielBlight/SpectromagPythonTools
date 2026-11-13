"""Synthetic scan-boundary tests. No native SDK or hardware access."""

import threading
from types import SimpleNamespace

import pytest
from test_combined_feedback import backend

from spectromagpythontools.devices.pgm1se import GimbalError
from spectromagpythontools.routines.scan_position_cache import scan_position_reuse
from spectromagpythontools.routines.scan_settings import active, scan_settings


def fixture(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    mount = SimpleNamespace(
        _backend=b,
        _lock=threading.RLock(),
        _prepared=True,
        _faulted=False,
        x_channel=1,
        y_channel=2,
        _require=lambda: None,
        _healthy=lambda state: None,
    )
    mount.position = lambda: b.read_channels((1, 2))
    return mount, b, calls


def test_full_checks_only_at_boundaries_even_after_clock_expiry(monkeypatch):
    mount, b, calls = fixture(monkeypatch)
    with scan_settings(mount):
        assert active(b)
        calls.clear()
        b._settings_checked_at = -100
        for _ in range(4):
            b.read_motion_channels((1, 2))
        assert not any(c[0] == "settings" for c in calls)
        assert sum(c[0] == "status" for c in calls) == 8
    assert not active(b)
    assert sum(c[0] == "settings" for c in calls) == 2
    assert mount._fast_checked_at is None
    assert mount._last_scan_settings["policy"] == "scan_boundaries"


def test_manual_periodic_checks_restored_after_exit(monkeypatch):
    mount, b, calls = fixture(monkeypatch)
    with scan_settings(mount):
        pass
    calls.clear()
    b._settings_checked_at = -100
    b.read_motion_channels((1, 2))
    assert sum(c[0] == "settings" for c in calls) == 2


def test_failed_entry_never_starts_scope(monkeypatch):
    mount, b, _ = fixture(monkeypatch)

    def fail():
        raise GimbalError("entry fault")

    mount.position = fail
    with pytest.raises(GimbalError, match="entry fault"), scan_settings(mount):
        pytest.fail("scan must not start")
    assert not active(b)


def test_end_failure_faults_mount(monkeypatch):
    mount, b, _ = fixture(monkeypatch)

    def fail(channels):
        raise OSError("end fault")

    with pytest.raises(OSError, match="end fault"), scan_settings(mount):
        b.read_channels = fail
    assert not active(b) and mount._faulted and not mount._prepared


def test_primary_exception_preserved_with_end_failure(monkeypatch):
    mount, b, _ = fixture(monkeypatch)

    def fail(channels):
        raise OSError("end fault")

    with (
        pytest.raises(RuntimeError, match="acquisition fault") as caught,
        scan_settings(mount),
    ):
        b.read_channels = fail
        raise RuntimeError("acquisition fault")
    assert any("end fault" in note for note in caught.value.__notes__)
    assert not active(b)


def test_nested_scope_rejected(monkeypatch):
    mount, _, _ = fixture(monkeypatch)
    with (
        scan_settings(mount),
        pytest.raises(RuntimeError, match="Nested"),
        scan_settings(mount),
    ):
        pass


def test_scope_applies_even_when_position_reuse_disabled(monkeypatch):
    mount, b, calls = fixture(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_SCAN_POSITION_REUSE", "0")
    with scan_position_reuse(mount):
        assert active(b)
    assert sum(c[0] == "settings" for c in calls) == 4


def test_verified_mode_unchanged(monkeypatch):
    mount, b, calls = fixture(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "verified")
    with scan_settings(mount):
        assert not active(b)
    assert calls == []


def test_missing_cache_fails_without_silent_refresh(monkeypatch):
    mount, b, calls = fixture(monkeypatch)
    with scan_settings(mount):
        calls.clear()
        b._motion_settings = {}
        with pytest.raises(GimbalError, match="cache lost"):
            b.read_motion_channels((1, 2))
        assert calls == []


def test_owner_thread_only(monkeypatch):
    mount, b, _ = fixture(monkeypatch)
    observed = []
    with scan_settings(mount):
        thread = threading.Thread(target=lambda: observed.append(active(b)))
        thread.start()
        thread.join()
        assert active(b)
    assert observed == [False]


def test_real_mount_uses_boundary_scope_without_midscan_full_read(monkeypatch):
    from test_pgm1se import make

    from spectromagpythontools.devices.pgm1se import Position, RawState
    from spectromagpythontools.routines.scan_position_cache import accept_position

    mount, _ = make()
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    mount._backend = b
    mount._connected = True
    mount._prepared = True
    # Match the fixture's synthetic status bits to the production validator.
    b._dll.PPC2_GetStatusBits = lambda serial, channel: 0x80000401
    b._dll.PPC2_GetPosition = lambda serial, channel: 0
    b._motion_settings = {1: (0, 3), 2: (0, 3)}
    b._io = lambda channel: SimpleNamespace(control_source=0, feedback_source=3)
    with scan_position_reuse(mount):
        assert active(b)
        # Seed an accepted coordinate as an ordinary completed pixel would.
        state = RawState(1, 0, 0x80000401, 2, 0, 3, 0)
        accept_position(mount, Position(0, 0, state, state))
        calls.clear()
        mount._fast_checked_at = -100
        b._settings_checked_at = -100
        # Keep cache signature consistent with the synthetic clock change.
        mount._scan_position_reuse_state["settings_checked_at"] = -100
        result = mount.move_and_wait(
            0, 0, tolerance_mrad=0.005, stable_s=0.05, timeout_s=1
        )
        assert result == (0, 0)
        assert not any(call[0] == "settings" for call in calls)
        assert sum(call[0] == "status" for call in calls) == 2
    assert not active(b)
