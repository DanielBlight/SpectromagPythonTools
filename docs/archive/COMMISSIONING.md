# SpectromagPythonTools: mirror control and photocurrent-map GUI

## Scope and readiness

A small Tk/ttk desktop application with connection/calibration, mirror-control
and photocurrent-map tabs. It reuses the existing device wrappers and scan_map;
there is no second scan engine or Labtools framework. This is a commissioning
implementation, NOT a hardware-validated unattended measurement system.

The November 10 17:24:57 snapshot was complete and was used as the baseline.
It contains committed PGM1SE work and untracked Keithley source/tests/docs.
The installer preserves that Git state and does not stage or commit anything.
The old validation files remain historical evidence, not current validation.

## Install and validate

Run the guarded apply_spectromag_gui_v1.py installer with --apply, then:

    uv sync
    uv run ruff format src/spectromagpythontools/configuration.py src/spectromagpythontools/gui src/spectromagpythontools/scripts/commissioning_gui.py src/spectromagpythontools/routines/photocurrent_map.py tests/test_commissioning.py
    uv run ruff check --fix src/spectromagpythontools/configuration.py src/spectromagpythontools/gui src/spectromagpythontools/scripts/commissioning_gui.py src/spectromagpythontools/routines/photocurrent_map.py tests/test_commissioning.py
    uv run python -m pytest -q

PyVISA is now declared as a runtime dependency. uv sync regenerates uv.lock;
the authoring environment could not resolve/download your Windows dependencies.
Tk is a Python interpreter component, not a pip dependency. Check it tonight:

    uv run python -m tkinter

A small Tk demonstration window should open. If _tkinter is unavailable, this
interpreter lacks Tcl/Tk: use a Python 3.12 installation that includes Tcl/Tk
and recreate the project environment with that interpreter. Do not pip-install
an unrelated package named tkinter. The application has no Qt dependency.

The authoring environment lacks _tkinter and a display, so the actual desktop
layout has NOT been rendered or manually exercised. Headless widget-contract
fixtures test callbacks and state, not real Tk behaviour. The installer does not
claim that the native Windows GUI or hardware was validated.

## Launch tonight without hardware

From the project root:

    uv run python -m spectromagpythontools.scripts.commissioning_gui --tab mirror

Or select the map tab initially:

    uv run python -m spectromagpythontools.scripts.commissioning_gui --tab map

Both commands launch the SAME application. Do not run two copies for the same
instruments. A per-project OS file lock prevents two copies for the same project;
it cannot detect other applications or copies of the project elsewhere.

The default file is config/instruments.local.toml. Use --config PATH to select
another file. Existing local configuration is never overwritten by the installer.
If missing, copy config/instruments.example.toml to instruments.local.toml.
The example contains placeholders and no calibration or sample-safe limits.
Launching/loading configuration does NOT connect to hardware. There is no fake
measurement/demo mode: no displayed current or map is fabricated. Grey pixels
mean unacquired. The initial small grid is a UI convenience, NOT a safe-area claim.

## Controls

Connections/calibration:
- Edit serial, VISA resource, model/channel, SDK directory and timeouts.
- List USB VISA resources without selecting the first result automatically.
- Identify Keithley using its existing connection sequence, which forces the
  selected SMU output high impedance; it is not a read-only electrical operation.
- Optional count-conversion fields plus evidence and explicit verification.
- All edits are IN MEMORY ONLY. To persist them, edit the local TOML in VS Code.
- Mirror connection/calibration fields are locked while the mirror is connected.

Mirror control:
- Connect without issuing a position/enable command.
- Read raw SDK state even if angular calibration is absent.
- Explicit Prepare closed loop, with a confirmation because it may move the beam.
- Go to X/Y targets, single-click X/Y jogs, and explicit Move to 0,0.
- Jog from the latest SDK readback, not a previous GUI target.
- No automatic motion on editing a field, no auto-repeat keyboard motion.
- Disconnect leaves the last demand active; 0,0 is NOT an emergency-safe position.

Photocurrent map:
- X/Y start/end and point counts (1..100 per axis, at most 10,000 GUI pixels).
- For a single point on an axis, start and end must match.
- Required user-entered DUT voltage/current limits and current compliance.
- Bias, NPLC, optional fixed current range, local/remote sense, sample count,
  pixel settling and serpentine selection.
- Read current once without connecting/moving the mirror.
- New CSV path, measurement label and explicit start confirmation.
- Live signed-current heatmap, pixel progress and last readback/current.
- Grey = not acquired, white = zero, red = positive, blue = negative.
  Color scale is symmetric about zero and rescales to acquired values.
- First requested Y row is drawn at the bottom. Axis labels show the requested
  angular coordinates, while each CSV row also records actual SDK readback.

## Threading, cancellation and cleanup

One non-daemon worker owns all device calls; Tk widgets are touched only on the
main thread. Only one operation can be outstanding. Manual moves, identification,
config changes and new scans are disabled while a command is running. Stop sets
a shared cancellation Event; it is NOT queued behind the scan. Native DLL calls
cannot be forcibly interrupted, so stop/close waits for in-flight calls to return.
The mirror's host timeout is not a hard bound on a blocked native call.

Keithley connections are scoped to identify, single-read or map operations, not
left persistently enabled between button clicks. A map/read disables output on
normal completion and attempts cleanup on error/cancellation. Communication
failure can leave output-off UNCONFIRMED; check the physical instrument.
Mirror failures stop the operation without automatic parking or rollback. A
second-axis failure can leave the first axis at its new position. Reconnect
faulted sessions before continuing. Close cancels, waits, disconnects, then exits;
there is no forced thread kill or promise that disconnect removes drive voltage.

GUI logs go to logs/gui-TIMESTAMP.log and are ignored by Git. Logs contain serials,
identity and error details; review before sharing. CSVs are opened exclusively,
never overwritten, and flushed after each pixel. A failed/cancelled map retains
partial pixels. Check the final CSV comment for complete/partial status. A disk
failure can prevent the footer. The GUI retains the partial heatmap after errors.

scan_map adds optional on_pixel and acquisition_metadata parameters. Existing
callers are unchanged. Callbacks run after the row is saved; a callback failure
stops acquisition and marks the saved rows partial. Metadata is namespaced under
acquisition and includes mirror serial, channels, conversion evidence, motion
settings, measurement label and cached-readback limitation. Existing Keithley
settings and raw readings remain recorded. This is total illuminated/dark current,
not automatic dark subtraction. Preserve separate light and dark acquisitions.

## Tomorrow's bounded commissioning sequence

1. With the laser blocked, verify the matched PGM1S/PPC102/cables and channel
   labels. Power OFF before making piezo connections. Follow the vendor manual.
2. Install matching-bitness Kinesis/drivers and the appropriate VISA backend.
   Close Kinesis and other instrument clients before Python connects.
3. Enter the real serial and USB VISA resource/model. List/identify each device
   independently. Confirm the exact Keithley model rather than assuming 2602B.
4. Connect the mirror and read raw state. Save the log. No conversion is required
   for this inspection and no positioning is enabled by connecting.
5. Resolve the installed SDK's count-to-mrad conversion from its device-specific
   documentation or Thorlabs confirmation, cross-checking small known positions.
   Do not infer full-range scaling from the synthetic test fixtures or discover
   it with blind endpoint moves. Enter verified X/Y conversions and evidence.
   Disconnect/reconnect after changing conversion settings.
6. Explicitly prepare closed loop with the laser blocked. Read position, try one
   small jog in each direction, and check axis direction, scale and settling.
   Validate USB-loss/stale-cache behaviour separately before unattended use.
7. With the mirror stationary, enter DUT-approved limits and test one Keithley
   reading. Confirm output-off on completion. No default current is sample-safe.
8. Choose a small grid inside the experimentally confirmed safe beam path, a NEW
   CSV, and conservative settling/integration. Check the acknowledgement, start
   the map and verify the CSV plus live display. Test Stop on a short acquisition.
9. Acquire a separate dark map if needed. Do not interpret mirror mrad as sample
   micrometres without a measured optical calibration.

## Remaining blockers, not bypassed by the GUI

- Native PPC102 count-to-angle conversion remains explicitly unverified until
  confirmed for the installed SDK and matched device. Prepare/motion require it.
- SDK getters are cached; request-plus-delay does not certify a fresh response.
  Host timestamps are not instrument timestamps. The acknowledgement records
  awareness, not a freshness fix or safety certification.
- No laser/shutter interlock, hardware-triggered fly scan, optical sample-space
  calibration or real hardware validation is included.
- The existing driver command sets and native ABI have NOT been changed here.

If conversion cannot be verified tomorrow, use the application for raw mirror
inspection and stationary Keithley tests only; do not bypass the gate to map.
