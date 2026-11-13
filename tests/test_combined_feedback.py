"""Synthetic DLL tests for combined feedback requests. No hardware access."""

from types import SimpleNamespace

import pytest

from spectromagpythontools.devices import pgm1se
from spectromagpythontools.devices.pgm1se import PGM1SE, GimbalError, KinesisPPC102


def backend(monkeypatch):
    calls = []

    class DLL:
        def PPC2_RequestSettings(self, serial, channel):
            calls.append(("settings", channel))
            return 0

        def PPC2_RequestStatus(self, serial, channel):
            calls.append(("status", channel))
            return 0

        def PPC2_RequestActualPosition(self, serial, channel):
            calls.append(("actual", channel))
            return 0

        def PPC2_GetPosition(self, serial, channel):
            calls.append(("position", channel))
            return channel * 123

        def PPC2_GetStatusBits(self, serial, channel):
            calls.append(("bits", channel))
            return 0x80000401

        def PPC2_GetPositionControlMode(self, serial, channel):
            calls.append(("mode", channel))
            return 2

    b = KinesisPPC102()
    b._serial, b._dll = b"95000001", DLL()
    b._motion_settings = {1: (0, 3), 2: (0, 3)}
    b._settings_checked_at = pgm1se.time.monotonic()
    b._io = lambda channel: SimpleNamespace(control_source=0, feedback_source=3)
    monkeypatch.setattr(
        pgm1se.time, "sleep", lambda seconds: calls.append(("wait", seconds))
    )
    monkeypatch.delenv("SPECTROMAG_FEEDBACK_REQUEST", raising=False)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    return b, calls


def test_legacy_default_retains_both_requests(monkeypatch):
    b, calls = backend(monkeypatch)
    states = b.read_motion_channels((1, 2))
    assert calls[:5] == [
        ("status", 1),
        ("actual", 1),
        ("status", 2),
        ("actual", 2),
        ("wait", 0.1),
    ]
    assert [s.raw_position for s in states] == [123, 246]
    assert b._last_feedback_read["mode"] == "legacy"


def test_combined_motion_batches_status_and_keeps_wait_and_getters(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    states = b.read_motion_channels((1, 2))
    assert calls[:3] == [("status", 1), ("status", 2), ("wait", 0.1)]
    assert not any(c[0] == "actual" for c in calls)
    assert sum(c[0] == "position" for c in calls) == 2
    assert sum(c[0] == "bits" for c in calls) == 2
    assert sum(c[0] == "mode" for c in calls) == 2
    assert [s.raw_position for s in states] == [123, 246]
    assert all(s.feedback_source == 3 for s in states)
    profile = b._last_feedback_read
    assert profile["mode"] == "combined" and profile["actual_position_requests"] == 0
    assert all(profile[k] >= 0 for k in ("requests_s", "response_wait_s", "getters_s"))


@pytest.mark.parametrize("reader", ["read_channels", "read_motion_channels"])
def test_full_refresh_retains_legacy_requests(monkeypatch, reader):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    b._settings_checked_at = -100
    getattr(b, reader)((1, 2))
    assert sum(c[0] == "actual" for c in calls) == 2
    assert sum(c[0] == "settings" for c in calls) == 2
    assert b._last_feedback_read["mode"] == "legacy"


def test_verified_policy_never_uses_combined(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "verified")
    b.read_motion_channels((1, 2))
    assert sum(c[0] == "actual" for c in calls) == 2


def test_invalid_option_rejected_before_requests(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "invalid")
    with pytest.raises(ValueError, match="legacy or combined"):
        b.read_motion_channels((1, 2))
    assert calls == []


@pytest.mark.parametrize("channels", [(), (1, 1), (3,), (True,), (1.0,)])
def test_invalid_channels_do_not_send_requests(monkeypatch, channels):
    b, calls = backend(monkeypatch)
    with pytest.raises(ValueError, match="distinct"):
        b._read_channels(channels, refresh_settings=False, combined_status=True)
    assert calls == []


def test_request_error_propagates_without_cached_acceptance(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    b._dll.PPC2_RequestStatus = lambda serial, channel: 4
    with pytest.raises(GimbalError, match="RequestStatus"):
        b.read_motion_channels((1, 2))
    assert not any(c[0] == "position" for c in calls)


@pytest.mark.parametrize("bits", [0, 0x80000401 | 0x800, 0x80000401 | 0x1000])
def test_combined_does_not_bypass_mount_health_checks(monkeypatch, bits):
    b, _ = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    b._dll.PPC2_GetStatusBits = lambda serial, channel: bits
    state = b.read_motion_channels((1, 2))[0]
    with pytest.raises(GimbalError):
        PGM1SE._healthy(state)


def test_settings_missing_forces_full_refresh(monkeypatch):
    b, calls = backend(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined")
    b._motion_settings = {}
    b.read_motion_channels((1, 2))
    assert sum(c[0] == "settings" for c in calls) == 2
    assert sum(c[0] == "actual" for c in calls) == 2


def test_feedback_summary_handles_old_and_new_profiles(tmp_path):
    import json

    from spectromagpythontools.routines.feedback_summary import enrich_feedback_summary

    csv = tmp_path / "map.csv"
    result = {}
    enrich_feedback_summary(result, csv)
    assert result == {}
    path = csv.with_suffix(".motion.txt")
    path.write_text("{}\n")
    enrich_feedback_summary(result, csv)
    assert result == {}
    read = {
        "mode": "combined",
        "refresh_settings": False,
        "status_requests": 2,
        "actual_position_requests": 0,
        "requests_s": 0.01,
        "response_wait_s": 0.1,
        "getters_s": 0.002,
    }
    path.write_text(json.dumps({"feedback_reads": [read, read]}) + "\n")
    enrich_feedback_summary(result, csv)
    summary = result["feedback_read_summary"]
    assert summary["reads"] == summary["combined_reads"] == 2
    assert summary["response_wait_s"] == 0.2
    assert summary["actual_position_requests"] == 0
