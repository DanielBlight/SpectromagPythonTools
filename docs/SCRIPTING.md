# Writing scripts

Use device wrappers for connections, routines for acquisition/motion coordination,
and your script for experiment order. No GUI is needed. Importing the examples
or running `--help` does not connect hardware.

## Setup

Run from `C:\SpectromagPythonTools` using the configured interpreter:

```powershell
Set-Location 'C:\SpectromagPythonTools'
$env:PYTHONPATH = 'C:\SpectromagPythonTools\src'
$py = 'C:\ProgramData\anaconda3\envs\spectromag\python.exe'
```

Close the GUI/Kinesis first. The examples use real hardware when run, not a
simulation. Check the sample's electrical limits and allowed mirror travel.
There is no laser/shutter control. Use `with` blocks so exceptions and Ctrl+C
trigger normal instrument cleanup; forcibly killing Python bypasses cleanup.

## 1. Get a Keithley measurement

Runnable example: `examples/keithley_measurement.py`.

```powershell
& $py .\examples\keithley_measurement.py --config '.\config\instruments.local.toml'
```

The example loads measurement values from `[photocurrent_map]`, opens the selected
channel, configures it, enables output only inside a context, and returns a Point.
For a custom script:

```python
from spectromagpythontools.configuration import build_smu, load_config, measurement
from spectromagpythontools.routines.photocurrent_map import read_point

config = load_config('config/instruments.local.toml')
max_v, max_i, settings = measurement(config['photocurrent_map'])
with build_smu(config['keithley'], max_abs_bias_v=max_v, max_current_a=max_i) as smu:
    smu.configure(settings)
    with smu.output_enabled():
        reading = smu.read()  # One current/voltage reading.
        point = read_point(smu, samples=3, settle_s=0.1)
        print(reading.current_a, reading.voltage_v)
        print(point.mean_current_a, point.sample_std_current_a)
        print(point.readings)  # Individual readings are retained.
```

`read_point` does not enable output for you. Its sample standard deviation is
undefined (`None`) for one sample. Output is disabled when the output context
exits, including on normal Python exceptions.

## 2. Connect the mirror, prepare closed loop and move when needed

Runnable example: `examples/mirror_moves.py`.

```powershell
& $py .\examples\mirror_moves.py --config '.\config\instruments.local.toml' --x-mrad 0 --y-mrad 0
```

The essential sequence is:

```python
import os
from spectromagpythontools.configuration import build_mount, load_config

config = load_config('config/instruments.local.toml')
os.environ['SPECTROMAG_MIRROR_POLICY'] = 'verified'
with build_mount(config['pgm1se']) as mirror:  # Connects, does not move.
    mirror.prepare_closed_loop()             # Explicit hardware preparation.
    actual_x, actual_y = mirror.move_and_wait(0.0, 0.0)
    # Take measurements or do other experiment work here.
    actual_x, actual_y = mirror.move_and_wait(0.1, -0.1)
```

Moves are absolute mechanical mrad, block until settled, and return SDK readback
coordinates. They enforce configured travel bounds. Closed-loop preparation is
required after each new connection. Disconnecting does not return the mirror to
zero. The complete example restores the previous policy environment variable.
Readback is SDK-cached, not a certified fresh hardware timestamp.

## 3. Repeat a photocurrent map

Runnable example: `examples/repeatable_map.py`.

```powershell
& $py .\examples\repeatable_map.py --config '.\config\instruments.local.toml' --repeats 3
```

Edit `xs`, `ys`, `samples`, `settle_s` and `serpentine` in `main()` once to define
the experiment. The shipped example is a 5 by 5 grid from -0.1 to +0.1 mrad on
each axis, three readings per pixel, 0.1 s extra settling. It is a real scan.
Electrical settings come from the profile, not remembered GUI values.
Each run gets a new timestamped CSV. Verified motion is explicitly selected.

To use the same function from your own script, run from the repository root:

```python
from pathlib import Path
from examples.repeatable_map import acquire

Path('data').mkdir(exist_ok=True)
result = acquire(
    'config/instruments.local.toml', 'data/my-map-001.csv',
    xs=(-0.1, 0.0, 0.1), ys=(-0.1, 0.0, 0.1),
    samples=3, settle_s=0.1, serpentine=True,
)
frame = result.to_dataframe()  # pandas DataFrame, same columns as CSV.
current_a = result.to_numpy() # numpy.ndarray, shape (3, 3), amperes.
```

Use a NEW filename on every call. The routine never overwrites a CSV. Repeatable
means the same commanded grid/settings, not guaranteed identical physical
measurements. Keep the config fixed between repeats. CSV metadata records the
measurement settings, axes, averaging, mount calibration and motion settings.

For already connected/configured devices, call the routine directly:

```python
from spectromagpythontools.routines.gimbal_positioning import gimbal_mover
from spectromagpythontools.routines.map_result import scan_map_result

# smu is connected/configured; mirror is connected/prepared in closed loop.
result = scan_map_result(
    smu, xs=(-0.1, 0.0, 0.1), ys=(-0.1, 0.0, 0.1),
    move_and_wait=gimbal_mover(mirror), output_csv='data/another-new-map.csv',
    samples=3, settle_s=0.1, serpentine=True, position_unit='mrad',
)
```

The caller owns device lifetimes. `scan_map_result` uses the existing `scan_map`
engine and then reads its CSV; it does not change averaging or motion behaviour.
The original `scan_map` still returns its integer pixel count for compatibility.
Pass a `threading.Event` as `cancel` to both the mover and scan for cooperative
cancellation. Exceptions propagate and the partial CSV is retained.

## 4. DataFrame and array conventions

- `result.to_dataframe()` preserves CSV column order and acquisition row order.
  `readings_json` stays a JSON string; use `json.loads` to inspect raw readings.
  Metadata/footer are in `frame.attrs['metadata']` and `frame.attrs['footer']`.
- `result.to_numpy()` contains `mean_current_a`, not the standard deviation or
  individual samples. Shape is `(number_of_y_points, number_of_x_points)`.
- `current_a[row, column]` corresponds to `result.ys[row]`, `result.xs[column]`.
  Supplied axis order is preserved, including descending axes. Serpentine changes
  acquisition order, not the array orientation. Do not blindly reshape CSV rows.
- These are measured currents under illumination. No dark-current subtraction
  is applied automatically; a true light-minus-dark photocurrent needs a separate
  dark/reference measurement under the same settings.

Load an existing map without connecting anything:

```python
from spectromagpythontools.routines.map_result import load_map_result

result = load_map_result('data/my-map-001.csv')
frame = result.to_dataframe()
current_a = result.to_numpy()

# Deliberate recovery of a stopped/failed map:
partial = load_map_result('data/stopped-map.csv', allow_partial=True)
partial_current_a = partial.to_numpy()  # Unmeasured pixels are NaN, never zero.
```

Partial files are rejected by default. Duplicate/out-of-range pixel indices and
inconsistent completion counts are rejected rather than silently reshaped.

## 5. Python dependencies and Spyder

NumPy is already a dependency of the existing Matplotlib GUI stack. pandas is
optional and imported only by `to_dataframe()`. Check the actual lab interpreter:

```powershell
& $py -c "import numpy; print(numpy.__version__)"
& $py -c "import pandas; print(pandas.__version__)"
```

If pandas is absent on the offline PC, transfer compatible pandas and dependency
wheels into the existing wheelhouse, then install without network access:

```powershell
& $py -m pip install --no-index --find-links 'C:\SpectromagPythonTools\wheelhouse' pandas
```

Use the actual wheelhouse directory if different. Array/CSV output still works
without pandas. No dependency metadata or lockfile is changed by this cleanup.
In Spyder use the spectromag interpreter, set the working directory to the project
root and add `src` to its Python path. Keep execution under `if __name__ == '__main__':`
so importing a script does not start an experiment.
