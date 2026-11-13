"""Per-user operational preferences only; no hardware or GUI imports."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

FIELDS = frozenset(
    {
        "x",
        "y",
        "jog_step",
        "mirror_tolerance_mrad",
        "mirror_stable_s",
        "mirror_timeout_s",
        "response_wait_s",
        "x_start",
        "x_end",
        "nx",
        "y_start",
        "y_end",
        "ny",
        "bias_v",
        "max_current_a",
        "nplc",
        "current_range_a",
        "samples",
        "settle_s",
        "measurement_label",
    }
)
FLAGS = frozenset({"remote_sense", "serpentine"})


def state_path(config_path):
    identity = os.path.normcase(str(Path(config_path).expanduser().resolve()))
    key = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    base = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / ".local" / "share"))
    return base / "SpectromagPythonTools" / "gui" / f"settings-{key}.json"


def clean(values, flags):
    if not isinstance(values, dict) or not isinstance(flags, dict):
        raise TypeError("Invalid preference dictionaries")
    safe_values = {
        key: value
        for key, value in values.items()
        if key in FIELDS and isinstance(value, str) and len(value) <= 2048
    }
    safe_flags = {
        key: value
        for key, value in flags.items()
        if key in FLAGS and type(value) is bool
    }
    return {"schema": 1, "values": safe_values, "flags": safe_flags}


def load_preferences(config_path):
    path = state_path(config_path)
    if not path.exists():
        return clean({}, {})
    if path.stat().st_size > 131072:
        raise ValueError("Preference file is too large")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if (
        not isinstance(data, dict)
        or type(data.get("schema")) is not int
        or data["schema"] != 1
    ):
        raise ValueError("Unsupported preference schema")
    return clean(data.get("values", {}), data.get("flags", {}))


def save_preferences(config_path, values, flags):
    data = clean(values, flags)
    path = state_path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=".settings-",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(data, stream, indent=2, allow_nan=False)
            stream.flush()
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path
