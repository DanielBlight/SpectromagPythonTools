# Reconnect and motion profiling checkpoint

## Changes
- Clear Python backend/DLL references and settings caches on close, including error paths.
- Clean up partially opened backends and failed initial session inspection.
- Before declaring a native connection ready, request full state on both axes and require actuator-connected bits. Retry only missing readiness, at most ten reads with a two-second host deadline checked between native calls. Fault flags fail immediately. Native calls themselves cannot be forcibly interrupted.
- Ensure GUI mainloop exit requests cooperative worker shutdown and joins the worker before the launcher releases its instance lock. Close does not park, disable channels, or change mirror demands.
- Buffer host timings for each move and write an uploadable map-*.motion.txt file after scan/output cleanup. No extra motion-time reads. Settings and acquisition are unchanged.
- Existing map_timing command includes sidecar fallback counts and phase totals. Fallback time includes its nested reads/commands: do not add overlapping categories to total_s. These are host-call durations, not mechanical response measurements.

## Validation
223 offline tests passed in the reconstructed uploaded project. Synthetic fixtures only; no hardware was accessed. Ruff 0.17.3 format and E4/E7/E9/F/I/B/BLE/SIM checks passed. The installer additionally runs the local project's configured Ruff checks and complete tests. Real Windows SDK reopen behaviour remains to be validated on the lab PC. No claim of a proven SDK root cause is made.

## Lab test
Restart the Spyder kernel once after installing. Launch, connect and prepare; close normally and wait for the prompt. Relaunch WITHOUT restarting the kernel, connect and prepare again. Do not ignore a connection-health error. Then run the same 5x5 map with unchanged settings and analyse the latest CSV. Upload its matching .motion.txt sidecar if needed. The last two measured reference scans were approximately 12.26 s total.

## Optional Git commands (development PC only)
git add -- src/spectromagpythontools/devices/pgm1se.py src/spectromagpythontools/gui/control.py src/spectromagpythontools/gui/commissioning.py src/spectromagpythontools/scripts/map_timing.py src/spectromagpythontools/routines/motion_profile.py tests/test_reconnect_motion_profile.py docs/RECONNECT_MOTION_PROFILE.md
git commit -m "Harden mirror session lifecycle and profile move phases"

## Next bounded continuation prompt
Review the same-kernel reopen result and the new map timing summary plus .motion.txt profile. Distinguish command, initial/full reads, post-move reads, fixed delay and verified fallback costs. Recommend one bounded next improvement without weakening health checks or changing acquisition/averaging settings. If reopen still fails, use the exact raw state/error to diagnose rather than assuming native cleanup is proven.
