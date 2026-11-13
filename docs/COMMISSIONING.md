# Engineering controls

Routine operation is documented in [USER_GUIDE.md](USER_GUIDE.md). Use the
engineering GUI only when you need explicit preparation/diagnostic controls:

```powershell
$env:PYTHONPATH = 'C:\SpectromagPythonTools\src'
$py = 'C:\ProgramData\anaconda3\envs\spectromag\python.exe'
& $py -m spectromagpythontools.scripts.commissioning_gui --config '.\config\instruments.local.toml' --commissioning
```

Connecting and preparing closed loop are separate engineering operations. Verify
controller/channel identity, feedback availability and matched-pair count
calibration before movement. Do not change calibration just to clear an error.
The ordinary operator GUI prepares closed loop as part of Connect.

Use `scripts/benchmark_speed_modes.py --help`, the packaged `map_timing` module,
and `inspect_pgm1se --help` for their available diagnostics. Retain these tools:
they support maintenance even though they are not part of routine operation.
Fast `one_check` movement is not equivalent to continuous verified settling;
SDK-cached readback does not certify freshness. Changing response waits or
feedback policy requires hardware validation, not only offline tests.
