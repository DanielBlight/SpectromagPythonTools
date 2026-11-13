"""Scan-scoped reuse of accepted SDK positions; no hardware access on import.

The GUI worker owns the scan. A reentrant mount lock excludes other threads.
No accepted coordinate survives scan exit, faults, fallback or lifecycle changes.
Cached SDK position is not proof of fresh hardware feedback.
"""

import os
import threading
from contextlib import contextmanager


def _signature(mount):
    return (
        id(mount._backend),
        id(mount.x_calibration),
        id(mount.y_calibration),
        mount.x_channel,
        mount.y_channel,
        mount.max_abs_mrad,
        mount._prepared,
        getattr(mount, "_faulted", False),
        getattr(mount._backend, "response_wait_s", None),
    )


def _state(mount):
    state = getattr(mount, "_scan_position_reuse_state", None)
    if state is None or state["owner"] != threading.get_ident():
        return None
    return state


def invalidate(mount):
    state = _state(mount)
    if state is not None:
        state["position"] = None
        state["signature"] = None
        state["settings_checked_at"] = None


def initial_position(mount):
    state = _state(mount)
    if state is not None and state["position"] is not None:
        if state["signature"] == _signature(mount) and state[
            "settings_checked_at"
        ] == getattr(mount._backend, "_settings_checked_at", None):
            state["reused"] += 1
            return state["position"]
        invalidate(mount)
    return mount._motion_position()


def accept_position(mount, position):
    state = _state(mount)
    if state is not None:
        state["position"] = position
        state["signature"] = _signature(mount)
        state["settings_checked_at"] = getattr(
            mount._backend, "_settings_checked_at", None
        )
        state["accepted"] += 1
    return position.x_mrad, position.y_mrad


@contextmanager
def _reuse_positions(mount):
    """Enable only around a scan, never around manual GUI movement.

    Holds the existing RLock throughout the scan. Unsupported synthetic/custom
    mounts retain their original behaviour. Real GUI operations are serialised.
    """
    if os.environ.get("SPECTROMAG_SCAN_POSITION_REUSE", "1") != "1" or not callable(
        getattr(mount, "_one_check_move", None)
    ):
        yield None
        return
    with mount._lock:
        if getattr(mount, "_scan_position_reuse_state", None) is not None:
            raise RuntimeError("Nested scan-position reuse is not supported")
        state = {
            "owner": threading.get_ident(),
            "position": None,
            "signature": None,
            "settings_checked_at": None,
            "reused": 0,
            "accepted": 0,
        }
        mount._scan_position_reuse_state = state
        try:
            yield state
        finally:
            invalidate(mount)
            del mount._scan_position_reuse_state


@contextmanager
def scan_position_reuse(mount):
    from spectromagpythontools.devices.scan_settings import scan_settings

    with scan_settings(mount), _reuse_positions(mount) as state:
        yield state
