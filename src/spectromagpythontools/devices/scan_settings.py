"""Scan-boundary configuration validation; no hardware access on import."""

import threading
import time
from contextlib import contextmanager


def active(backend):
    return getattr(backend, "_scan_settings_owner", None) == threading.get_ident()


@contextmanager
def scan_settings(mount):
    """Check settings at entry/exit; never skip per-move status/position checks.

    Only the one_check Kinesis path is changed. Manual/verified paths retain
    periodic checking. End checks run on success, cancellation and failure;
    an end-check failure never replaces an existing acquisition exception.
    """
    import os

    from spectromagpythontools.devices.pgm1se import KinesisPPC102

    backend = getattr(mount, "_backend", None)
    if os.environ.get("SPECTROMAG_MIRROR_POLICY") != "one_check" or not isinstance(
        backend, KinesisPPC102
    ):
        yield
        return
    with mount._lock:
        if getattr(backend, "_scan_settings_owner", None) is not None:
            raise RuntimeError("Nested scan-settings scope is not supported")
        mount._require()
        if not mount._prepared:
            raise RuntimeError("Prepare closed loop before starting a map")
        started = time.perf_counter()
        initial = mount.position()
        start_s = time.perf_counter() - started
        mount._fast_checked_at = time.monotonic()
        backend._scan_settings_owner = threading.get_ident()
        primary = None
        try:
            yield initial
        except BaseException as exc:
            primary = exc
            raise
        finally:
            del backend._scan_settings_owner
            end_started = time.perf_counter()
            try:
                # Explicit full read even after a fault; _require may reject a
                # faulted mount, so read/validate without allowing any movement.
                states = backend.read_channels((mount.x_channel, mount.y_channel))
                for state in states:
                    mount._healthy(state)
            except BaseException as exc:
                mount._faulted = True
                mount._prepared = False
                if primary is None:
                    raise
                primary.add_note(f"End-of-map settings validation failed: {exc}")
            finally:
                mount._fast_checked_at = None
                mount._last_scan_settings = {
                    "start_s": start_s,
                    "end_s": time.perf_counter() - end_started,
                    "policy": "scan_boundaries",
                }
