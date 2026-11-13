# Keithley code structure

This is SpectromagPythonTools, not Labtools.

- devices/keithley2600.py owns instrument communication and single readings.
- routines/photocurrent_map.py owns settling, averaging, scan order and CSV output.
- scripts call the routine; future GUIs should call the same routine.

The device API is smu.read(), returning a Reading.
For an averaged pixel use read_point(smu, samples=3, settle_s=0.1).
Import read_point and scan_map from
spectromagpythontools.routines.photocurrent_map.

The caller must exclusively own the instrument throughout an acquisition.
This refactor does not change the TSP commands or establish hardware validation.
The original validation log describes the earlier bundle, not this refactor.
