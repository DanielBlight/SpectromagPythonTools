"""Host-call diagnostics. No extra device reads or changed motion settings."""

import json
import os
import time
from contextlib import contextmanager
from functools import wraps


def timed(mount, phase, function, *args, **kwargs):
    record = getattr(mount, "_active_motion_profile", None)
    if record is None:
        return function(*args, **kwargs)
    started = time.perf_counter()
    try:
        return function(*args, **kwargs)
    finally:
        record[phase + "_s"] = (
            record.get(phase + "_s", 0.0) + time.perf_counter() - started
        )
        record[phase + "_calls"] = record.get(phase + "_calls", 0) + 1


def profile_move(function):
    @wraps(function)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            if getattr(self, "_active_motion_profile", None) is not None:
                return function(self, *args, **kwargs)
            record = {
                "schema": "mirror-motion-v1",
                "status": "error",
                "fallback": False,
            }
            record["policy"] = os.environ.get("SPECTROMAG_MIRROR_POLICY", "verified")
            cache = getattr(self, "_scan_position_reuse_state", None)
            reused_before = cache["reused"] if cache is not None else 0
            self._active_motion_profile = record
            started = time.perf_counter()
            try:
                result = function(self, *args, **kwargs)
                record["status"] = "complete"
                return result
            except BaseException as exc:
                record["error_type"] = type(exc).__name__
                record["error"] = str(exc)
                raise
            finally:
                record["total_s"] = time.perf_counter() - started
                record["cached_initial_reused"] = bool(
                    cache is not None and cache["reused"] > reused_before
                )
                self._last_motion_profile = record.copy()
                self._active_motion_profile = None
                records = getattr(self, "_motion_profile_records", None)
                if records is not None and len(records) < 10001:
                    records.append(record.copy())

    return wrapped


@contextmanager
def capture_motion(mount, path):
    """Buffer at most 10001 GUI moves; write after scan/output cleanup.

    Exclusive file creation precedes acquisition. Preserve acquisition errors.
    """
    if getattr(mount, "_motion_profile_records", None) is not None:
        raise RuntimeError("Nested motion profiling is not supported")
    with path.open("x", encoding="utf-8") as stream:
        records = []
        mount._motion_profile_records = records
        primary = None
        try:
            yield
        except BaseException as exc:
            primary = exc
            raise
        finally:
            del mount._motion_profile_records
            try:
                for index, record in enumerate(records):
                    stream.write(
                        json.dumps({"move_index": index, **record}, allow_nan=False)
                        + "\n"
                    )
                stream.flush()
            except Exception as exc:
                if primary is None:
                    raise
                primary.add_note(f"Motion profile write failed: {exc}")
