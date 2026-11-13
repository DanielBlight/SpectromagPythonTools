# Scan-only last accepted position reuse

Scope: GUI Session.map only, and only the existing one_check driver policy.
The context is placed around scan_map, not around manual moves or the whole GUI.
Verified mode is unchanged. No SDK delay, settling, acquisition, averaging, bias,
compliance, PID, calibration, fault mask or ctypes binding is changed.

The first pixel has no cache. After a successful post-command in-tolerance check,
the actual returned Position object can supply the next pixel's pre-command
coordinate. Post-command live status/position health checks still occur. Periodic
full checks remain. A fallback discards the cache; its next pixel starts fresh.
Fault, cancellation and scan exit discard the cache. Lifecycle connect/close/
prepare invalidate it. Backend/calibration/channel/limit/prepared/fault/SDK-wait
changes or a newer settings-refresh clock invalidate it before reuse.

The GUI worker serializes operations. The context holds the existing reentrant
mount lock for the scan, excluding other threads. No cached state survives to a
manual GUI move or another scan. Other applications must not control the same
hardware. This is NOT continuous feedback between pixels: an unobserved drift
can occur between the previous acceptance and the next command. The subsequent
post-command check/fallback is retained. Cached SDK values are not certified fresh
hardware samples. No callback/response acknowledgement is invented.

A metadata entry marks the scan-reuse capability. It does not imply every pixel
reused a coordinate, or that verified mode uses this optimization. Existing
profiling should show fewer motion_position_check calls on ordinary fast pixels.
No speedup is promised until an identical hardware map is measured.

Validation: 20 new regression cases passed against interface-faithful synthetic
driver source in the authoring environment. The exact supplied GUI Session.map
AST transformation compiles. Latest installed full driver was not in the supplied
GUI snapshot; the installer therefore checks its AST layout and stops before any
write if unexpected. The complete local suite and Ruff run on the lab PC. These
are offline tests, not hardware or full repository validation in authoring.

Installation: close GUI and Kinesis, apply using spectromag Python. Installer
backs up outside source and uses isolated user preferences/pytest temp. Failure
attempts byte-for-byte source rollback and removes new files. Explicit --restore
BACKUP_DIRECTORY refuses subsequent edits. Local TOML/data/preferences untouched.

Acceptance: restart Spyder kernel or use a fresh process. Keep same one_check
policy, SDK wait, delay, refresh interval, tolerance, NPLC, samples, range, scan
bounds and starting position. Run a small map with new filename, inspect measured
coordinates, complete footer and confirmed SMU output-off. Compare timing against
18.906 s total and 0.278 s motion/pixel baseline. Also check a manual jog and a
second map; neither should reuse the previous scan's cache. Cancellation should
retain partial CSV and existing output cleanup. No Git required on the lab PC.

Disable without source rollback: set SPECTROMAG_SCAN_POSITION_REUSE=0 before
launch. Default is 1. This does not change one_check/verified policy selection.
