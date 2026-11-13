# Operator GUI

Double-click scripts/launch_operator.cmd on the lab PC, or launch the existing
commissioning_gui module. The default screen is now the operator GUI.
Use --commissioning to open the original engineering interface deliberately.
Both use the same per-project instance lock and must not run together.

## Daily workflow

1. Launch. Loading a profile never connects, moves or enables output.
2. Click Connect instruments. Keithley is identified with output off, then the
   mirror is connected and prepared for closed-loop software control. No target
   position command is sent, but enabling channels/changing input mode can shift
   the beam. A failed connection/preparation closes the candidate mirror session.
3. Move mirror: enter X/Y in mechanical mrad and click Go to X/Y, or use jogs.
4. Run map: set bounds/counts, bias, compliance, label and output file. Advanced
   settings contain NPLC, samples, range, pixel settling, sense and scan order.
5. Start map. Stop requests cooperative cancellation and retains partial data.
6. Disconnect/close does not park or power down the mirror.

Keithley is checked on Connect but is not held open. Each acquisition reopens
and validates its session, and output is disabled/closed afterwards. The UI does
not claim a persistent SMU connection. A ready state is not continuous health
monitoring. Reconnect after a reported motion fault. Check physical output after
communication errors; software cannot confirm output-off after transport loss.

## Engineering profile

Calibration, channels, serials, VISA resources, SDK paths, travel limits and
response wait come from the TOML profile, not editable operator controls.
The installer migrates photocurrent_map mirror_tolerance_mrad to 0.005,
mirror_stable_s to 0.05, and preserves the existing mirror_timeout_s.
operator_motion holds policy=one_check, delay_s=0.08,
feedback_request=combined, full_check_s=5. Profile values override old engineering
GUI preferences and ambient motion environment variables for operator operations.
Environment is restored after each worker operation. No other client should
control the same instruments. Boundary configuration checks, per-move position/
status checks, tolerance fallback, calibration gates and electrical checks remain.
No driver command set or scan routine is rewritten. The commissioning interface
retains its previous acknowledgement behaviour; operator maps do not require or
record a fabricated acknowledgement. No laser/shutter interlock is implemented.

Operational preferences such as bias, compliance and averaging remain remembered.
Review them when changing samples. No sample-safe current limit is invented.
Existing acquisition defaults and saved measurement settings are not migrated.

## Display

Inferno remains default. Other options: magma, viridis, plasma, cividis, turbo,
gray, coolwarm and RdBu_r. Auto and fixed limits use the selected palette.
Zero-centred mode deliberately uses RdBu_r to distinguish signs. Unmeasured
pixels are grey; no interpolation invents detail. Colourbar uses A with SI prefixes.

## Validation and limitations

Offline synthetic instrument and headless widget tests plus Agg plot rendering.
Native Windows Tk layout, USB/VISA and real hardware require local acceptance.
Check connection, one manual move, a small map, Stop, disconnect and reopen before
leaving this version for other users. Compare motion settings in CSV/profile.
Boundary checks remain outside the pixel-loop elapsed timer.

## Development-PC transfer

Use scripts/export_source_transfer.py after acceptance. It now includes .cmd
launchers. Extract to a staging directory, compare/merge into the existing Git
checkout, validate and commit intended source. Local instrument TOML is excluded:
transfer it separately only if appropriate for that machine. Never replace .git.
