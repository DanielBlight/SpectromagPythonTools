# User guide

The instrument profile identifies the hardware and its calibration. The GUI is
for routine mirror positioning and mapping; it does not rewrite that profile.

## 1. Change instrument configuration

1. Close the GUI and any hardware scripts.
2. Make a copy of `config/instruments.local.toml` outside the repository.
3. Open the original in a text editor. Change only the relevant values below.
4. Save as UTF-8 TOML, restart the GUI and connect again. Profile changes require
   a disconnect/reconnect; they do not update a running session.

### `[keithley]`

- `resource`: full USB VISA resource string, including `::INSTR`. Use the resource
  reported by your VISA installation, not a guessed serial or product ID.
- `model`: exact supported model string, for example `2636B`.
- `channel`: `"a"` or `"b"`, matching the connected sample terminals.
- `timeout_ms`: communication timeout, normally left unchanged.
- `visa_library`: existing VISA backend selection; leave unchanged unless needed.
- `remote_sense`: GUI default for remote sensing. Enable only for correctly wired
  four-wire sensing. The scripting examples take this from `[photocurrent_map]`.

Example syntax only; retain your actual resource:

```toml
[keithley]
resource = "USB0::REPLACE_WITH_ACTUAL_VISA_RESOURCE::INSTR"
model = "2636B"
channel = "b"
timeout_ms = 5000
```

### `[pgm1se]`

- `serial`: PPC102 controller serial as a quoted string, eight digits starting `95`.
- `x_channel`, `y_channel`: distinct controller channels connected to the two axes.
- `kinesis_directory`: installed Kinesis DLL directory. TOML single quotes are
  convenient for Windows paths, for example `'C:\Program Files\Thorlabs\Kinesis'`.
- `max_abs_mrad`: allowed absolute mechanical mirror angle, no greater than 10 mrad.
  Use a smaller bound if required by the optical setup.
- `response_wait_s`: SDK response wait. Keep the commissioned value; reducing it
  is not a harmless way to speed up scans.

### `[pgm1se.x_calibration]` and `[pgm1se.y_calibration]`

Each axis needs `verified = true`, `raw_minus_10`, `raw_plus_10` and `evidence`.
These describe the verified raw-count conversion for the matched controller/mount.
Do not copy counts from another axis or set `verified` merely to bypass a check.
If the hardware pairing changes, remeasure and fit the conversion first:

```powershell
& $py -m spectromagpythontools.scripts.fit_pgm1se_conversion --help
```

Angles here are mechanical mirror mrad, not sample micrometres or optical beam
angle. A separate optical calibration is needed for sample-space coordinates.

### `[photocurrent_map]`

Measurement values used by the scripts:

```toml
[photocurrent_map]
max_abs_bias_v = 0.0
max_current_a = 1e-6
bias_v = 0.0
current_limit_a = 1e-6
nplc = 1.0
current_range_a = ""
remote_sense = false
samples_per_pixel = 3
pixel_settle_s = 0.1
mirror_tolerance_mrad = 0.005
mirror_stable_s = 0.05
mirror_timeout_s = 10.0
```

These are illustrative settings, not approved limits for every sample. Merge
needed keys into the existing table; do not create a second table of the same name.
`current_range_a = ""` selects autorange. A fixed range is a number in amperes.
`nplc` controls integration per reading; `samples_per_pixel` controls averaging;
`pixel_settle_s` is the extra pause after motion, before acquisition.

The operator GUI takes measurement defaults from the profile on first use, then
may restore per-user operational preferences. It derives its voltage bound from
the selected bias and its compliance from the displayed current limit. Therefore
review the displayed bias/current limit every time, rather than treating the
profile's script limits as a separate immutable GUI interlock.

`[operator_motion]` holds commissioned GUI motion policy/timing. Leave it unchanged
for routine use. The new scripting examples explicitly use verified motion, so
can be slower than a GUI profile using `one_check`.

## 2. Launch the GUI

Double-click `scripts/launch_operator.cmd`, or use PowerShell:

```powershell
Set-Location 'C:\SpectromagPythonTools'
$env:PYTHONPATH = 'C:\SpectromagPythonTools\src'
$py = 'C:\ProgramData\anaconda3\envs\spectromag\python.exe'
& $py -m spectromagpythontools.scripts.commissioning_gui --config '.\config\instruments.local.toml'
```

For a different interpreter, set `SPECTROMAG_PYTHON` before running the `.cmd`.
Use one application at a time. Close Kinesis and other instrument sessions.

## 3. Move the mirror

1. Check the displayed instrument profile and select **Connect**. The operator
   workflow connects the mirror, prepares closed loop and checks the Keithley.
2. In **Move mirror**, enter absolute X and Y angles in mrad and use the move control.
3. Read the returned position/status before proceeding. Centre commands `(0, 0)`;
   it is a real move, not just a display reset.
4. Use **Stop** to request cancellation. It waits for outstanding instrument I/O
   and cleanup; it is not an emergency hardware stop.

If preparation fails, do not force motion. Check strain-gauge feedback, channel
assignment and verified calibration. Reconnect after resolving the problem.

## 4. Acquire a map

1. In **Run map**, set X/Y start, end and number of points.
2. Review bias, current limit, samples per pixel and pixel settling time. Open
   advanced measurement settings for NPLC, range and sensing options.
3. Select the output location and run the map. Each pixel is moved to, settled,
   measured and written to CSV. Serpentine reverses alternate acquisition rows.
4. Inspect the live map and diagnostics. The optional PNG is a picture; the CSV
   is the measurement record. Existing-map viewing does not reacquire data.
5. Wait for completion before starting another operation or disconnecting.

CSV comments beginning `#` contain metadata and final status. Pixel rows include
requested/actual coordinates, mean current in A, sample standard deviation,
mean voltage, individual readings JSON and timing columns. A stopped/failed map
is partial, not a completed rectangle. Keep the CSV even if no PNG was produced.

## 5. Remembered settings and common problems

Operational preferences are stored per user/profile under
`%LOCALAPPDATA%\SpectromagPythonTools\gui\settings-*.json`, not in the TOML.
To reset them, close the GUI and rename the relevant JSON as a backup. Do not
remove instrument calibration to reset GUI preferences.

- Wrong instrument/channel: check the TOML and reconnect.
- No strain-gauge feedback: check feedback cabling and correct channels.
- Tk missing: run `& $py -m tkinter`; use a Python installation with Tk.
- Missing pandas: only DataFrame conversion needs pandas. NumPy conversion needs
  NumPy. Neither is required merely to load a result as Python records.
- Slow mapping: preserve averaging/NPLC unless intentionally changing the
  measurement. Motion verification and settling also contribute to duration.

The lab PC can remain offline. This cleanup requires no package reinstall.
