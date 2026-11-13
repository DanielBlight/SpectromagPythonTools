# Cleanup and scripting checkpoint

Based on the uploaded source snapshot, not a live edit of the lab PC.

## Changes

- Simplified README and user/development/engineering guides.
- Added USER_GUIDE.md and SCRIPTING.md.
- Added three runnable hardware examples and the map_result routine.
- Replaced duplicate calibration implementation with a compatibility launcher.
- Kept drivers, scan engine, GUI, tests, lockfile and useful diagnostics.
- Preserved local config, calibration, data, wheelhouse and environments.

## Removed reviewed redundant files

- `docs/archive/COMMISSIONING.md`
- `docs/archive/DEVELOPMENT.md`
- `docs/archive/GUI_REMEMBERED_SETTINGS.md`
- `docs/archive/gui_validation.txt`
- `docs/archive/keithley2600.md`
- `docs/archive/keithley2600_structure.md`
- `docs/archive/keithley2600_validation.txt`
- `docs/archive/MIRROR_REFRESH_COORDINATION.md`
- `docs/archive/OPERATOR_GUI.md`
- `docs/archive/pgm1se.md`
- `docs/archive/pgm1se_validation.txt`
- `docs/archive/RECONNECT_MOTION_PROFILE.md`
- `docs/archive/SCAN_POSITION_REUSE.md`
- `docs/archive/SPEED_CHECKPOINT.md`
- `docs/archive/speed_validation.txt`
- `docs/gui_validation.txt`
- `docs/keithley2600_structure.md`
- `docs/keithley2600_validation.txt`
- `docs/pgm1se_validation.txt`

## Validation

Baseline: 323 offline tests passed. Updated project: 341 offline tests passed.
Python compile checks and all three example --help invocations passed.
Tests use fake sessions/backends, not physical measurements. Windows GUI display,
VISA hardware, Kinesis DLLs and actual instrument behaviour were not tested here.
Ruff is unavailable in the authoring environment; run the documented local check.

## Install

Download apply_spectromag_cleanup.py into Downloads, close the GUI and scripts,
and run it with the lab Python and --root C:\SpectromagPythonTools.
It uses only the standard library and does not connect hardware or install packages.
Use --check for a read-only preview. It refuses changed reviewed files rather than
silently overwriting newer work. UTF-8 BOM/line endings/trailing blank lines are
normalised for comparison with the text snapshot.

Original bytes are backed up to a new sibling folder outside the repository.
The updater prints that folder. To undo, run the same updater with --rollback
followed by that folder path. Rollback refuses to overwrite files edited since
installation. Unknown files are never recursively deleted. The source archive
omits local config because it was absent from the upload; use the updater on the
existing lab checkout rather than replacing that checkout with the archive.
