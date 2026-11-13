"""Operator workflow on the existing serial worker and measurement routines."""

from spectromagpythontools.devices.keithley2600 import AcquisitionCancelled
from spectromagpythontools.gui.control import Session
from spectromagpythontools.gui.operator_profile import motion_environment


class OperatorSession(Session):
    requires_ack = False

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.keithley_checked = False

    def state(self):
        return {**super().state(), "keithley_checked": self.keithley_checked}

    def connect(self, config, cancel):
        if self.mount is not None:
            raise RuntimeError("Disconnect instruments before reconnecting")
        self.keithley_checked = False
        if cancel.is_set():
            raise AcquisitionCancelled("Connection cancelled")
        # Identify first, with output off. Do not enable output or apply bias.
        identity = self.identify(config, cancel)
        if cancel.is_set():
            raise AcquisitionCancelled("Connection cancelled")
        try:
            with motion_environment(config):
                super().connect(config, cancel)
                result = self.prepare(config, cancel)
            if cancel.is_set():
                raise AcquisitionCancelled("Connection cancelled")
        except BaseException as exc:
            try:
                super().disconnect({}, cancel)
            except BaseException as cleanup:  # noqa: BLE001
                exc.add_note(f"Connection cleanup failed: {cleanup}")
            raise
        self.keithley_checked = True
        return {
            **result,
            "identity": identity["identity"],
            "message": "Ready: mirror closed loop prepared; Keithley checked, output off. SMU sessions reopen for each acquisition.",
        }

    def disconnect(self, config, cancel):
        self.keithley_checked = False
        return super().disconnect(config, cancel)

    def move(self, config, cancel):
        with motion_environment(config):
            return super().move(config, cancel)

    def map(self, config, cancel):
        if not self.keithley_checked:
            raise RuntimeError("Connect instruments before mapping")
        with motion_environment(config):
            return super().map(config, cancel)

    def read_current(self, config, cancel):
        if not self.keithley_checked:
            raise RuntimeError("Connect instruments before measuring")
        return super().read_current(config, cancel)
