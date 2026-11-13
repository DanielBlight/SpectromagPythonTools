# SpectromagPythonTools

Control a Keithley 2600B-family SourceMeter and a Thorlabs PGM1SE/PPC102 mirror,
and acquire step-and-settle photocurrent maps.

## Start here

- [User guide](docs/USER_GUIDE.md): edit instrument configuration, launch the GUI,
  move the mirror, measure current and acquire/view maps.
- [Scripting guide](docs/SCRIPTING.md): measurements, closed-loop moves, repeatable
  maps, pandas DataFrames and 2D NumPy arrays.
- [Development](docs/DEVELOPMENT.md): offline tests, layout and maintenance.
- [Engineering controls](docs/COMMISSIONING.md): commissioning and diagnostics.

On the configured lab PC, double-click `scripts/launch_operator.cmd`.
The default interpreter is `C:\ProgramData\anaconda3\envs\spectromag\python.exe`.
The local instrument profile is `config/instruments.local.toml`.

The uploaded review snapshot did not include configuration files. The cleanup
updater preserves the installed profile, calibration, data and environment.
It does not create a replacement profile with guessed calibration.

## Runnable examples

From the repository root, with `src` on PYTHONPATH:

```powershell
$env:PYTHONPATH = "$PWD\src"
$py = 'C:\ProgramData\anaconda3\envs\spectromag\python.exe'
& $py .\examples\keithley_measurement.py --help
& $py .\examples\mirror_moves.py --help
& $py .\examples\repeatable_map.py --help
```

`--help` does not access hardware. Running without `--help` does.
Close the GUI and Kinesis before using a hardware script. Review sample bias,
compliance and mirror travel first. These routines do not control the laser or shutter.
