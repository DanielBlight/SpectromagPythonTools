# Mirror refresh coordination checkpoint

## Scope

- In one_check mode, backend polling and mount prechecks use the same configured
  SPECTROMAG_MIRROR_FULL_CHECK_S interval (default 2 seconds). A backend refresh
  updates the clock seen by the mount so it does not immediately repeat it.
- First move after lifecycle invalidation still performs a full check.
- Missing channel configuration cache always forces a refresh.
- Verified mode without environment overrides retains its original 1-second
  backend refresh interval and normal precheck/command/settling algorithm.
- If one-check acceptance misses tolerance, enter the existing verified polling
  loop WITHOUT repeating the full precheck or reissuing the target commands.
  This continuation flag is set only around fallback and reset in finally.
- Existing fault checks, cancellation, stable interval and remaining deadline
  are retained. A fault still invalidates preparation. No automatic park on fault.

## Unchanged / limitations

No ctypes signatures, fault masks, PID, calibration, range, NPLC, averaging,
compliance, voltage or current settings are changed. SDK response_wait_s remains
unchanged. Exclusive ownership is required. Configuration changes are detected
periodically, not immediately; interval is not a real-time guarantee because
native calls can block. Live position/status checks remain per pixel.

SDK getters are still cached, not certified fresh samples. No callback or queue
message is treated as position acknowledgement. RequestStatus and
RequestActualPosition are BOTH retained until their exact actual-position and
notification semantics are validated. Response-aware waiting remains deferred.

## Validation

Authoring checks: 10 new regression cases on synthetic interface fixtures,
plus transformed verified/default and continuation control-flow checks. Not a
real instrument or complete repository test. Installer runs Ruff format,
ruff check --fix and ALL local tests in a fresh disposable pytest base directory.
Failure restores original source and removes newly added checkpoint files.
No tests or installation steps access hardware.

## Comparison

Close GUI and Kinesis. Apply using spectromag Python. Launch the existing profiler
with one_check, delay 0.05, full check interval 2. Keep the same scan geometry,
starting position, tolerance, NPLC, sample count, range and electrical settings.
Use a new CSV path. Compare mean motion time, sdk_settings_refresh counts,
position_command counts and fallback counts. A fallback may still be counted as
verified_algorithm but should no longer cause repeated demand commands.

Expected direction: fewer redundant refreshes and cheaper fallback. No specific
speedup is promised before the hardware comparison. Keep verified mode available.

Restore: close GUI, run this installer with --restore BACKUP_DIRECTORY.
Restore refuses unexpected later edits. Local configuration/data are untouched.
