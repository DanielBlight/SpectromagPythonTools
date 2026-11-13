# Naming reference

This audit covers the supplied snapshot plus the delivered restructure. It is not
an inspection of unuploaded local edits. No hardware command, timing, config key,
CSV column or numerical unit is renamed. Existing Python names remain valid.

## Preferred public names

| Existing name | Clearer spelling | Meaning |
|---|---|---|
| devices.keithley2600.KeithleyPhotocurrent | Keithley2600 | One-channel fixed-bias current/voltage wrapper; no map or dark subtraction |
| devices.keithley2600.Settings | MeasurementSettings | Bias, compliance, NPLC, range and sensing; not map geometry |
| devices.keithley2600.Reading | MeasurementReading | One current/voltage/range reading and host time bounds |
| routines.photocurrent_map.Point | PointMeasurement | Averaged point and retained individual readings |
| routines.photocurrent_map.read_point | measure_point | Measure an already-positioned point; caller enables output |
| routines.photocurrent_map.scan_map | acquire_current_map | Acquire illuminated-current map; returns integer pixel count |
| routines.gimbal_positioning.gimbal_mover | make_mirror_mover | Build a blocking move callback for the scan engine |
| application.control.Session | InstrumentSession | Instrument ownership/lifecycle, not a VISA transport session |
| application.control.HardwareWorker | InstrumentWorker | Serial hardware-operation worker |
| application.operator_control.OperatorSession | OperatorInstrumentSession | Operator workflow using the shared instrument session |
| gui.operator.Application | OperatorApplication | Operator Tk view |
| gui.commissioning.Application | CommissioningApplication | Engineering Tk view |
| data.map_files.read_map | load_map_for_display | Offline CSV loader returning axes, cells and status |
| cli.commissioning_gui (default operator) | cli.operator_gui | Clearly named operator launch command |

Keithley2600/MeasurementSettings/MeasurementReading are the canonical class names;
legacy names are identity aliases. Other descriptive spellings are identity
aliases to established implementations, avoiding algorithm or monkeypatch churn.
Old pickled objects resolve through retained legacy module attributes; new
objects display the clearer Keithley class names in repr/type diagnostics.

## Names retained deliberately

- PGM1SE and KinesisPPC102 identify real mount/controller models. AxisCalibration,
  Position, RawState, MapResult, MapFigure and MapPlot are clear in their modules.
- configuration.measurement constructs instrument settings from a profile. It
  does not measure. It remains for compatibility; use MeasurementSettings directly
  when writing an explicit configuration rather than parsing a profile.
- xs/ys are ordered coordinate sequences, not physical sample coordinates. _mrad,
  _a, _v, _s and _ms suffixes are units, not arbitrary abbreviations.
- photocurrent_map is a legacy module/profile name. Values are measured total
  current under illumination, not automatically light-minus-dark photocurrent.
  acquire_current_map is the more accurate new spelling. No data is relabelled.
- map_result.scan_map_result acquires and returns MapResult; to_dataframe and
  to_numpy convert that result. Do not confuse it with acquire_current_map's count.
- Private helpers finite, number, active and timed remain module-local vocabulary;
  renaming them creates churn without improving the public scripting interface.
- The generic package main placeholder is outside this naming-only checkpoint.
  Use the documented GUI/module commands, not the placeholder as a launcher.

## Examples

```python
from spectromagpythontools.devices.keithley2600 import (
    Keithley2600, MeasurementSettings, MeasurementReading,
)
from spectromagpythontools.routines.photocurrent_map import measure_point
from spectromagpythontools.routines.gimbal_positioning import make_mirror_mover
```

Launch the operator view with:

```powershell
uv run python -m spectromagpythontools.cli.operator_gui --config config/instruments.local.toml
```

No dependencies or lockfile changes are required. The original launchers continue
working. GUI-independent imports remain hardware-inert. Alias tests and the full
regression suite verify compatibility.
