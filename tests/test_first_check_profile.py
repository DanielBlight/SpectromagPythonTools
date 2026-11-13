"""First-check diagnostics tests; no SDK or hardware access."""

import json
from types import SimpleNamespace

import pytest

from spectromagpythontools.routines.first_check_profile import (
    enrich_summary,
    record_first_check,
)


def position(x, y):
    return SimpleNamespace(x_mrad=x, y_mrad=y)


@pytest.mark.parametrize(
    "measured_x,expected", [(1.0, True), (0.0, False), (0.9995, True)]
)
def test_first_check_values(measured_x, expected):
    mount = SimpleNamespace(_active_motion_profile={})
    record_first_check(
        mount,
        position(0, 0),
        position(measured_x, 0),
        1,
        0,
        0.001,
        0.05,
        abs(measured_x - 1),
    )
    check = mount._active_motion_profile["first_check"]
    assert check["within_tolerance"] is expected
    assert check["error_x_mrad"] == measured_x - 1
    assert check["unchanged_from_initial"] is (measured_x == 0)
    json.dumps(check, allow_nan=False)


def test_inactive_profile_is_noop():
    mount = SimpleNamespace()
    record_first_check(mount, None, None, 1, 0, 0.001, 0.05, 0)
    assert not hasattr(mount, "_active_motion_profile")


def test_existing_raw_state_is_recorded():
    mount = SimpleNamespace(_active_motion_profile={})
    p = position(0, 0)
    p.x_state = SimpleNamespace(raw_position=123, status_bits=1)
    record_first_check(mount, p, p, 1, 0, 0.001, 0.05, 1)
    check = mount._active_motion_profile["first_check"]
    assert check["measured_x_raw"] == 123 and check["initial_x_status_bits"] == 1


def test_summary_and_old_profiles(tmp_path):
    csv = tmp_path / "map.csv"
    result = {}
    enrich_summary(result, csv)
    assert result == {}
    path = csv.with_suffix(".motion.txt")
    path.write_text("{}\n")
    enrich_summary(result, csv)
    assert result == {}
    mount = SimpleNamespace(_active_motion_profile={})
    record_first_check(mount, position(0, 0), position(0, 0), 1, 0, 0.001, 0.05, 1)
    path.write_text(json.dumps(mount._active_motion_profile) + "\n")
    enrich_summary(result, csv)
    summary = result["first_check_summary"]
    assert summary["outside_tolerance"] == 1
    assert summary["unchanged_from_initial_outside_tolerance"] == 1
    assert summary["max_abs_error_mrad"] == 1


def test_real_fast_path_diagnostics_preserve_reads_and_fallback(monkeypatch):
    from test_mirror_fast_policy import Fixture, move

    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    mount = Fixture()
    mount._active_motion_profile = {}
    mount.miss = True
    move(mount)
    check = mount._active_motion_profile["first_check"]
    assert check["within_tolerance"] is False
    assert mount.fallback == 1 and mount.full == 1 and mount.fast == 1
    assert len(mount.commands) == 1
