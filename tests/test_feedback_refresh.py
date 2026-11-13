"""Combined periodic-refresh regressions. Synthetic DLL only."""

import json

import pytest
from test_combined_feedback import backend

from spectromagpythontools.devices.pgm1se import GimbalError
from spectromagpythontools.routines.feedback_summary import enrich_feedback_summary


def enable(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_FEEDBACK_REQUEST", "combined_refresh")


def test_periodic_refresh_keeps_settings_and_omits_duplicate_position(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    b._settings_checked_at = -100
    b.read_motion_channels((1, 2))
    assert calls[:5] == [
        ("settings", 1),
        ("status", 1),
        ("settings", 2),
        ("status", 2),
        ("wait", 0.1),
    ]
    assert not any(c[0] == "actual" for c in calls)
    assert b._last_feedback_read["refresh_settings"]
    assert b._last_feedback_read["mode"] == "combined"
    assert len(b._last_feedback_read["request_calls"]) == 4
    assert b._settings_checked_at > 0


def test_explicit_inspection_still_uses_legacy(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    b.read_channels((1, 2))
    assert sum(c[0] == "settings" for c in calls) == 2
    assert sum(c[0] == "actual" for c in calls) == 2
    assert b._last_feedback_read["mode"] == "legacy"


def test_verified_policy_still_uses_legacy(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "verified")
    b._settings_checked_at = -100
    b.read_motion_channels((1, 2))
    assert sum(c[0] == "actual" for c in calls) == 2


def test_missing_settings_are_requested_not_skipped(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    b._motion_settings = {}
    states = b.read_motion_channels((1, 2))
    assert sum(c[0] == "settings" for c in calls) == 2
    assert len(states) == 2 and all(s.feedback_source == 3 for s in states)


def test_per_api_times_are_nonnegative_and_match_request_count(monkeypatch):
    b, _ = backend(monkeypatch)
    enable(monkeypatch)
    b._settings_checked_at = -100
    b.read_motion_channels((1, 2))
    profile = b._last_feedback_read
    calls = profile["request_calls"]
    assert sum(c["api"] == "RequestSettings" for c in calls) == 2
    assert sum(c["api"] == "RequestStatus" for c in calls) == 2
    assert all(c["elapsed_s"] >= 0 for c in calls)
    assert sum(c["elapsed_s"] for c in calls) <= profile["requests_s"]


def test_failed_settings_request_stops_before_getters(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    b._settings_checked_at = -100
    b._dll.PPC2_RequestSettings = lambda serial, channel: 4
    with pytest.raises(GimbalError, match="RequestSettings"):
        b.read_motion_channels((1, 2))
    assert not any(c[0] == "position" for c in calls)
    assert b._settings_checked_at == -100


def test_summary_groups_old_and_new_reads(tmp_path):
    csv = tmp_path / "map.csv"
    read = {
        "mode": "combined",
        "refresh_settings": True,
        "status_requests": 2,
        "actual_position_requests": 0,
        "requests_s": 0.5,
        "response_wait_s": 0.05,
        "getters_s": 0.001,
        "request_calls": [
            {"api": "RequestSettings", "channel": 1, "elapsed_s": 0.25},
            {"api": "RequestSettings", "channel": 2, "elapsed_s": 0.24},
        ],
    }
    old = {**read, "refresh_settings": False, "requests_s": 0.001}
    del old["request_calls"]
    csv.with_suffix(".motion.txt").write_text(
        json.dumps({"feedback_reads": [read, old]}) + "\n"
    )
    result = {}
    enrich_feedback_summary(result, csv)
    summary = result["feedback_read_summary"]
    assert summary["by_read_type"]["settings_refresh"]["requests_s"] == 0.5
    assert summary["by_read_type"]["ordinary_motion"]["reads"] == 1
    assert summary["by_request_api"]["RequestSettings"]["calls"] == 2
    assert summary["request_calls_recorded"] == 2


def test_refresh_interval_unchanged(monkeypatch):
    b, calls = backend(monkeypatch)
    enable(monkeypatch)
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")
    b._settings_checked_at = -100
    b.read_motion_channels((1, 2))
    calls.clear()
    b.read_motion_channels((1, 2))
    assert not any(c[0] == "settings" for c in calls)
    assert b._coordinated_settings_interval() == 2
