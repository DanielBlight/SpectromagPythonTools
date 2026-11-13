# Speed checkpoint v1: preserve three-reading averaging

## What changed

1. Mirror: a full settings/status/position check still precedes both commands
   on EVERY move. During settling, request status and actual position for both
   channels, wait once, and read both. Do not repeatedly request settings or
   GetIOSettings on each settling observation. Refresh settings during polling
   after one second, or if no cache exists. Position/mode/status checks and the
   existing fault masks, calibration gate, deadlines and stable interval remain.
2. Keithley: read_point uses read_many for samples >1. Each instrument-side TSP
   loop acquires at most three raw current/voltage pairs and returns them in one
   response. Larger sample counts are split into batches of three. Averaging and
   sample standard deviation remain in the routine, not the device wrapper.
3. CSV schema is photocurrent-map-v2. Existing columns remain, with motion_s,
   settle_elapsed_s and acquisition_s appended. The final complete/partial
   footer includes timing_totals_s and elapsed_s. A summary module reads these
   without contacting hardware.

## What is NOT changed

No NPLC, autozero, source/measurement delay, range, filter, compliance, bias,
calibration, angular limit, tolerance, stable interval or pixel settling value
is changed. No new package is needed. Local configuration, GUI code, data and
Git state are untouched. Existing single-reading read() remains available.

## Explicit trade-offs and limitations

- Exclusive ownership is required: close Kinesis and all other VISA clients.
- Input/feedback configuration is now cached BETWEEN full checks. Configuration
  changes by another application are not detected immediately during settling;
  a full check occurs before every move and after one second of polling. Native
  call duration means this is not a hard real-time one-second guarantee.
- Position/status getters remain cached SDK observations, not certified fresh
  hardware samples. The optimization does not fix USB-loss/freshness semantics.
- Compliance, selected output state and errorqueue.count are checked on the
  instrument before and after EVERY conversion. A failure rejects the entire
  pixel batch and attempts high impedance locally; host cleanup also runs.
  Error queue entries are NOT cleared. No reset or global TSP function is loaded.
- pcall catches TSP runtime errors, but cannot guarantee output-off on firmware,
  transport or power failure. Verify output locally after an acquisition fault.
- Stop is checked between batches, not within a batch of up to three readings.
  Hardware compliance remains active throughout. Native/measurement calls cannot
  be forcibly interrupted. VISA timeout is unchanged; high NPLC/autorange may
  require a deliberately increased timeout if a batch exceeds the current one.
- Batched readings share host_started_unix_s/host_finished_unix_s for the whole
  batch. timestamp_basis explicitly says these are NOT per-conversion times.
  No instrument timestamps are fabricated. Raw current, voltage and range for
  each reading are retained.
- Three readings and arithmetic statistics are preserved, but the shorter gaps
  can change noise correlation or transient sampling. Compare repeatability as
  well as scan duration. No numerical speedup is promised before hardware tests.
- Timing motion_s includes command/SDK polling/stability; acquisition_s includes
  all SMU conversions and communication. save_s includes CSV serialisation and
  flush, not physical disk durability. callback_s measures callback invocation,
  not asynchronous Tk rendering. Footer elapsed includes output enable/disable
  and file handling, but not GUI connection/configuration before scan_map.
- Partial timing totals describe completed stages/pixels and may omit time in a
  failed in-flight stage. Missing footer after a disk failure remains possible.

## Apply and validate

Close Spectromag and Kinesis. Run the guarded apply_spectromag_speed_v1.py
installer using the spectromag interpreter with --apply. It checks the supplied
snapshot AST, backs up outside the project, applies the bounded files, runs Ruff
format, Ruff check --fix and the COMPLETE local tests directory. If validation
fails it restores original bytes/removes newly added files. Read its external
validation.log. No hardware is contacted by the installer or offline tests.

The authoring environment tested the supplied device/routine tests plus new
speed regressions. The supplied snapshot did not contain configuration.py,
gui/commissioning.py or gui/instance.py, so the full GUI suite could not be run
there. The installer therefore runs the complete suite on the actual lab copy.

## Hardware acceptance

1. First use Read current once with the same approved DUT limits, bias, 1 NPLC,
   three readings and settling as the last successful stationary measurement.
   Confirm three readings, reasonable statistics and output-off. This is the
   first real firmware validation of the new TSP command.
2. With the laser blocked, repeat a small positive/negative mirror jog and
   return to the starting point. Keep the existing tolerance/stable interval.
3. Repeat the same 5x5 map, same starting point, same measurement settings and
   NEW CSV path. Retain three readings and the existing current-range choice.
4. Check 25 pixels, three raw readings per pixel, position errors within the
   chosen tolerance, complete footer and physical output-off. Compare signal
   repeatability and timing, not just elapsed duration.
5. Run a short cancellation test. The batch may finish before Stop takes effect.
   Confirm partial CSV retention and output-off; mirror is not auto-parked.

Summary command (Anaconda Prompt):

    set PYTHONPATH=C:\SpectromagPythonTools\src
    python -m spectromagpythontools.scripts.map_timing "C:\SpectromagPythonTools\data\NEW_MAP.csv"

PowerShell:

    $env:PYTHONPATH = "C:\SpectromagPythonTools\src"
    & "C:\ProgramData\anaconda3\envs\spectromag\python.exe" -m spectromagpythontools.scripts.map_timing "C:\SpectromagPythonTools\data\NEW_MAP.csv"

If hardware fails, stop rather than rerunning the scan or increasing limits.
Preserve the error log and confirm output-off locally. Use installer --restore
BACKUP_DIRECTORY to restore this checkpoint; it refuses to overwrite unexpected
subsequent source changes. Never restore while the GUI/instrument worker runs.
