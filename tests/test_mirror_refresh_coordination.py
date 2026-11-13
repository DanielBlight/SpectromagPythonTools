"""Offline regressions for coordinated refresh and continuation fallback."""

import ast
import inspect
import textwrap

import pytest

from spectromagpythontools.devices.pgm1se import PGM1SE, KinesisPPC102


def backend(monkeypatch, *, checked=100, cache=True):
    from spectromagpythontools.devices import pgm1se

    monkeypatch.setattr(pgm1se.time, "monotonic", lambda: 101.5)
    obj = object.__new__(KinesisPPC102)
    obj._motion_settings = {1: (0, 1), 2: (0, 1)} if cache else {}
    obj._settings_checked_at = checked
    calls = []
    obj._read_channels = lambda channels, **kw: calls.append((channels, kw)) or ()
    return obj, calls


def test_fast_uses_shared_two_second_interval(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.delenv("SPECTROMAG_MIRROR_FULL_CHECK_S", raising=False)
    obj, calls = backend(monkeypatch)
    obj.read_motion_channels((1, 2))
    assert calls[-1][1]["refresh_settings"] is False


def test_verified_default_preserves_one_second_interval(monkeypatch):
    monkeypatch.delenv("SPECTROMAG_MIRROR_POLICY", raising=False)
    monkeypatch.delenv("SPECTROMAG_MIRROR_FULL_CHECK_S", raising=False)
    obj, calls = backend(monkeypatch)
    obj.read_motion_channels((1, 2))
    assert calls[-1][1]["refresh_settings"] is True


def test_expired_refresh_still_occurs(monkeypatch):
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")
    obj, calls = backend(monkeypatch, checked=99)
    obj.read_motion_channels((1, 2))
    assert calls[-1][1]["refresh_settings"] is True


def test_empty_cache_requires_refresh(monkeypatch):
    obj, calls = backend(monkeypatch, checked=101, cache=False)
    obj.read_motion_channels((1, 2))
    assert calls[-1][1]["refresh_settings"] is True


@pytest.mark.parametrize("value", ["0", "6", "nan"])
def test_invalid_interval_rejected(monkeypatch, value):
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", value)
    obj, calls = backend(monkeypatch)
    with pytest.raises(ValueError):
        obj.read_motion_channels((1, 2))
    assert calls == []


def test_verified_precheck_and_commands_guarded_only_on_continuation():
    tree = ast.parse(textwrap.dedent(inspect.getsource(PGM1SE._verified_move_and_wait)))
    guards = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.If)
        and "_fast_continue_settling" in ast.unparse(node.test)
    ]
    assert len(guards) == 2
    assert any("self.position()" in ast.unparse(node) for node in guards)
    assert any("set_position" in ast.unparse(node) for node in guards)
    # Health/position polling and deadline code remain outside these guards.
    source = ast.unparse(tree)
    assert "_motion_position" in source and "deadline" in source


def test_fallback_flag_is_reset_even_when_verified_fails(monkeypatch):
    from test_mirror_fast_policy import Fixture, move

    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.05")
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")
    obj = Fixture()
    obj.miss = True
    # Bind updated method to the synthetic fixture, not hardware.
    obj._one_check_move = PGM1SE._one_check_move.__get__(obj)
    seen = []

    def fail(*args, **kwargs):
        seen.append(obj._fast_continue_settling)
        raise RuntimeError("synthetic fallback failure")

    obj._verified_move_and_wait = fail
    with pytest.raises(RuntimeError, match="synthetic fallback"):
        move(obj)
    assert seen == [True]
    assert obj._fast_continue_settling is False
    assert obj._faulted and not obj._prepared


def test_backend_refresh_prevents_duplicate_mount_full_check(monkeypatch):
    from test_mirror_fast_policy import Fixture, move

    from spectromagpythontools.devices import pgm1se

    monkeypatch.setenv("SPECTROMAG_MIRROR_POLICY", "one_check")
    monkeypatch.setenv("SPECTROMAG_MIRROR_DELAY_S", "0.05")
    monkeypatch.setenv("SPECTROMAG_MIRROR_FULL_CHECK_S", "2")
    obj = Fixture()
    obj._one_check_move = PGM1SE._one_check_move.__get__(obj)
    now = pgm1se.time.monotonic()
    obj._fast_checked_at = now - 3
    obj._backend._settings_checked_at = now
    move(obj)
    assert obj.full == 0
    assert obj.fallback == 0
