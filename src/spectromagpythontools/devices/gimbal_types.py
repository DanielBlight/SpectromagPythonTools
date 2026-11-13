"""Gimbal value types and errors; no vendor or acquisition dependencies."""

from __future__ import annotations

import math
from dataclasses import dataclass


class GimbalError(RuntimeError):
    """Controller or acquisition state is unsuitable for positioning."""


class MoveCancelled(GimbalError):
    """No further commands will be sent; the last demand may remain active."""


def _finite(value, name):
    if isinstance(value, bool):
        raise TypeError(f"{name} must be numeric, not bool")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


@dataclass(frozen=True)
class AxisCalibration:
    """Verified SDK count mapping for -10 and +10 mrad, for ONE channel.
    No default scaling is supplied: generic SDK docs disagree about signed
    setpoints. Confirm the mapping against the matched controller's Kinesis
    display/API documentation. Do not determine it by blind full-range moves.
    """

    raw_minus_10: int
    raw_plus_10: int
    evidence: str

    def __post_init__(self):
        for value in (self.raw_minus_10, self.raw_plus_10):
            if type(value) is not int or not -32767 <= value <= 32767:
                raise ValueError("Calibration endpoints must be signed 16-bit counts")
        if self.raw_minus_10 == self.raw_plus_10:
            raise ValueError("Calibration endpoints must differ")
        if not isinstance(self.evidence, str) or not self.evidence.strip():
            raise ValueError("Record how the angular conversion was verified")

    def to_raw(self, mrad):
        angle = _finite(mrad, "angle_mrad")
        if not -10 <= angle <= 10:
            raise ValueError("Mechanical angle must be within +/-10 mrad")
        return round(
            self.raw_minus_10
            + (angle + 10) / 20 * (self.raw_plus_10 - self.raw_minus_10)
        )

    def from_raw(self, raw):
        if type(raw) is not int or not -32767 <= raw <= 32767:
            raise GimbalError("Invalid raw position")
        return -10 + 20 * (raw - self.raw_minus_10) / (
            self.raw_plus_10 - self.raw_minus_10
        )

    @property
    def resolution_mrad(self):
        return 20 / abs(self.raw_plus_10 - self.raw_minus_10)


@dataclass(frozen=True)
class RawState:
    channel: int
    raw_position: int
    status_bits: int
    mode: int
    input_source: int
    feedback_source: int
    host_unix_s: float


@dataclass(frozen=True)
class Position:
    x_mrad: float
    y_mrad: float
    x_state: RawState
    y_state: RawState
