# PGM1SE / PPC102 for SpectromagPythonTools

## Status

The current project snapshot has now been inspected. The commissioning GUI uses
this existing driver through configuration.py and gui/control.py, without changing
its native command set, calibration gate or cached-readback limitations.
See USER_GUIDE.md for operation and COMMISSIONING.md for engineering controls.
This remains an offline-tested, not hardware-validated, implementation.

## Files and responsibilities

- devices/pgm1se.py: device connection, SDK bindings, closed-loop positioning.
- routines/gimbal_positioning.py: small callback adapter for the existing map loop.
- scripts/inspect_pgm1se.py: read-only SDK state inspection, explicit serial required.
- tests/test_pgm1se.py: fake backend and mocked native-call tests.

No mapping, current measurement, raster generation, CSV output, Qt or GUI logic
is in the device wrapper. No new Python runtime dependency is needed: ctypes is
part of Python. Real hardware needs matching-bitness Windows Kinesis installed
separately, including its native PrecisionPiezo and dependent DLLs/drivers.
Vendor DLLs are not bundled. The native loader is not exercised on Linux.

## Hardware facts and sources

The PGM1SE kit is a PGM1S mount plus matched PPC102 controller. Closed-loop
mechanical travel is +/-10 mrad PER AXIS, not +/-10 mrad beam deflection or a
sample-space distance. The supplied manual is ETN038569-D03, Rev B March 2022,
particularly printed pages 1, 10-17, 22-24 and 30. Preserve the matched mount,
controller, cables and channel labels. Allow the manual's 30-minute warm-up for
precision work. Switch controller power OFF before connecting/disconnecting the
mount; rear connectors can carry hazardous piezo drive voltage.

The API signatures, enums, packed IO structure and status bits are from
C++/Kinesis/Benchtop/PPC00X/Thorlabs.MotionControl.Benchtop.PrecisionPiezo.h in the
supplied Thorlabs/Motion_Control_Examples snapshot, commit
3ef959523e2be48d57704af964f7a2c24547b722. Use PPC2_ for the multi-channel controller,
not PPC_ single-channel positioning or generic PBC_ functions.
The supplied APT protocol is DOC-103274 Issue 48, 10 August 2026, particularly
printed pages 217, 222, 228-230 and 238. Raw APT transport is NOT implemented.
No vendor example code was copied into this module; bindings follow its header.

## Important unresolved angular conversion

The mount manual establishes +/-10 mrad, but does not establish the native
PPC2_SetPosition count-to-angle mapping. The supplied C header documents a
0..32767 SET range, yet a -32767..32767 GET range. The generic APT position
command also documents a nonnegative set range. Those descriptions do not
justify assuming either:

- -32767..32767 means -10..10 mrad; OR
- 0..32767 means -10..10 mrad.

Neither assumption is installed as a default. Both axes require AxisCalibration
with verified raw counts at -10 and +10 mrad and a written evidence description.
The class supports an affine mapping, including reversed axis direction. It is
valid only if that affine relation has been established for the installed SDK
and matched device. The endpoints describe a conversion; they are NOT commands
to visit the endpoints. Do not discover scaling by blind full-range moves.
Obtain the SDK's device-specific conversion/calibration or confirmation from
Thorlabs, and cross-check against Kinesis displayed mrad at small controlled
reference positions with the laser blocked. Do not use synthetic test values.
If native negative demands are unsupported, the SDK error stops acquisition;
the wrapper does not retry or invent an alternative representation.

## API and state changes

Construction/import: no I/O or DLL load.
connect(): opens the explicit 95xxxxxx PPC102 serial and requests settings/state;
no enable, mode change, centring, zeroing or position command is sent.
raw_state(): returns raw SDK state even without angular calibration.
prepare_closed_loop(): requires both calibrations, checks actuator/feedback,
selects software-only input, closed-loop mode 2 and enables both channels.
This explicit operation CAN move the beam as external/front-panel contributions
are removed or channels are enabled. Block the laser first. It preserves other
IO fields, PID, filters, zero datum and stored calibration; it never persists
settings or resets the controller. It does not restore the previous input mode.
position(): checks mode, feedback, enable, input source and fault bits, returning
SDK-reported mechanical angles plus raw status and host observation timestamps.
move_and_wait(x_mrad,y_mrad): validates BOTH targets before sending either, sets
the channels sequentially, then waits for both SDK readbacks to stay within
tolerance for a stable interval. These are not simultaneous axis commands.
close(): disconnects WITHOUT parking, disabling, zeroing or changing the demand.
It does not power down the controller. No position is universally safe for a beam.

A lower max_abs_mrad can restrict the requested range further, but cannot enlarge
it beyond 10 mrad. This bounds commanded targets, not servo overshoot, external
physical movement or sample-plane illumination. The default 0.01 mrad tolerance
and 0.2 s stability interval are illustrative, not experimentally validated.

## Readback freshness and timeout limitation

The C DLL getters return cached values. The backend requests settings, status and
ACTUAL position, waits a configurable response interval, then reads that cache.
A successful request plus a delay does NOT prove receipt of a fresh hardware
sample. Host timestamps are observation times, not device sample timestamps.
Repeated stable cache observations do not establish independent fresh samples.
Validate the installed SDK's communication-loss behaviour before unattended
mapping. This implementation is NOT a stale-data-proof motion interlock.

The host timeout is checked around native calls. It cannot interrupt a blocking
vendor DLL call; it is not a hard real-time deadline. Cancellation is cooperative
between calls and during host waits. On failure/cancellation, no rollback,
centring or additional move is issued, and the instance is faulted after a move
has started. One axis may have moved before the second fails. The last demand
may remain active. Use an independent shutter/interlock and inspect the setup.
Exclusive ownership is required: close Kinesis GUI and other controller clients
before Python connects. Do not share the mount between acquisition threads.

## Read-only inspection

After installation, run (supply the actual serial, not a sample number):

    uv run python -m spectromagpythontools.scripts.inspect_pgm1se --serial YOUR_SERIAL

An alternate Kinesis installation can be selected with --kinesis-directory.
This connects and requests SDK state, but does not prepare or move the mount.
It is not a calibration procedure and does not certify closed-loop accuracy.

## Existing photocurrent map integration

After separately establishing the calibrations and configuring the Keithley:

```python
from spectromagpythontools.devices.pgm1se import PGM1SE
from spectromagpythontools.routines.gimbal_positioning import gimbal_mover
from spectromagpythontools.routines.photocurrent_map import scan_map

# verified_x and verified_y are real AxisCalibration objects, not test fixtures.
with PGM1SE(serial, x_calibration=verified_x, y_calibration=verified_y) as mount:
    mount.prepare_closed_loop()  # Explicit state-changing step; block laser first.
    scan_map(
        smu,
        xs=x_angles_mrad,
        ys=y_angles_mrad,
        move_and_wait=gimbal_mover(mount, cancel=cancel),
        output_csv=output_path,
        position_unit="mrad",
        cancel=cancel,
    )
```

This produces an ANGULAR map. Sample-plane micrometres require a separately
measured optical calibration including axis orientation, mirror reflection and
relay magnification. Do not label mirror mrad as sample micrometres. This is a
step-and-settle scan, not hardware-triggered continuous scanning.

## Validation and next bounded task

The bundled tests use SYNTHETIC counts and no real hardware. They test limits,
calibration arithmetic, state checks, fault handling, cancellation, timeout,
axis mapping, cleanup, IO-field preservation and native error propagation.
They do not validate the DLL ABI against an installed Windows SDK, actual angle
conversion, USB-loss detection, settling, sample coordinates or existing project
integration. Ruff was unavailable in the authoring environment; run local lint.

For hardware checks, use the engineering controls described in COMMISSIONING.md
and retain the verified angular conversion. Preserve the simple
device/routine split and reuse the existing map engine. Do not add an automatic
full-range calibration sweep or bypass the conversion gate.
