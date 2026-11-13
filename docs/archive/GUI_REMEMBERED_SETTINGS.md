# Remembered GUI settings and simplified electrical controls

## Changes

Only Bias (V) and Max current / compliance (A) remain as the primary electrical
controls. Max current is passed BOTH as the DUT current ceiling and as the
programmed SMU compliance. The hidden wrapper voltage ceiling is derived as
abs(bias), not a separately editable sample ceiling. Existing software caps
(20 V and 0.1 A), model-family minimum compliance, finite-value validation,
configuration readback, compliance rejection and output-off handling remain.
No claim is made that 20 V or 0.1 A is safe for a particular sample.

The GUI remembers operational fields per Windows user AND per resolved TOML path:
- bias, max current, NPLC, sample count, current range, settling, measurement label;
- grid bounds/counts, target X/Y, jog step, mirror tolerance/stability/timeout;
- SDK response wait, remote sense and serpentine.

Connection identity, serial, channels, model, VISA resource/backend, calibration,
calibration acknowledgement and software angular limit still come from TOML or
current-session edits. Environment-variable motion policy/delay/refresh settings
are NOT persisted by this change; keep using the existing launcher.

No automatic connection, preparation, motion or output enable occurs on restore.
Acknowledgement is false at startup; output CSV gets a fresh timestamp. Restored
settings, including bias, are revalidated before use. Review restored bias/current
when changing samples. Numeric entry strings can be remembered mid-edit; an
invalid remembered value cannot bypass action validation.

## Persistence

%LOCALAPPDATA%\SpectromagPythonTools\gui\settings-<config-path-hash>.json

Files are written atomically after a 600 ms edit debounce, before a submitted
operation and on accepted close. A corrupt/unsupported file is ignored with a log
message. Write failures are logged without disrupting hardware cleanup. No local
TOML, source or experimental data is overwritten by preference saving. To reset,
close the GUI and remove the corresponding settings JSON (or move it aside).

Legacy TOML max_current_a/current_limit_a are migrated conservatively by taking
the smaller explicit value. Existing command-line measurement() callers retain
their original explicit-limit interface; only GUI config derives the hidden keys.

## Validation

18 new preference/headless GUI cases and two existing headless GUI cases passed
in the authoring environment. Real GUI/configuration/preferences and Keithley
settings code were used. Missing mirror/routine modules were replaced with inert
IMPORT shims only for authoring tests; no shim is included in the installer.
This is not a physical Tk rendering test or hardware test. The complete project
suite and Ruff run on the lab PC during installation. The test environment uses
isolated LOCALAPPDATA so it cannot read or overwrite real user preferences.

Installer checks supplied-source AST fingerprints, backs up outside the project,
validates with ruff format, ruff check --fix and all local tests using a fresh
pytest base directory. Failure rolls source back. --restore BACKUP_DIRECTORY
restores only if no subsequent file edits are detected. It never removes real
user preference files. No dependencies, Git mutations or hardware commands.

## Acceptance

Close GUI/Kinesis, apply, launch via the existing normal/profiling launcher.
Change NPLC, samples, bias/current, scan grid and mirror settings; close/reopen.
Verify fields restore, CSV filename changes, connection/prepared remain false.
Confirm only two electrical controls, then use the same approved stationary test.
Motion/acquisition driver code and scan algorithms are unchanged by this update.
