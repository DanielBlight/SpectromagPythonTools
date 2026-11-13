# Keithley 2600B photocurrent wrapper

## Scope and integration status

Integrated into SpectromagPythonTools, independently of Labtools.
The device module is src/spectromagpythontools/devices/keithley2600.py.
Settling, averaging and mapping are in routines/photocurrent_map.py.
The commissioning GUI reuses these modules; see docs/COMMISSIONING.md.
No instrument commands were changed by the GUI checkpoint.

This is an original, explicit TSP/PyVISA implementation, not a vendored copy of the
example project's dynamic Lua bridge. No keithley2600 package is required.

## Files and offline validation

- devices/keithley2600.py: instrument connection, settings and single read().
- routines/photocurrent_map.py: read_point(smu, ...) and scan_map(...).
- tests/test_keithley2600.py: original fake-session tests.
- tests/test_commissioning.py: GUI-control and map integration tests.

Run from the project root:

    uv run ruff check --fix .
    uv run python -m pytest -q

Real acquisition needs PyVISA (declared in pyproject.toml) and a working VISA
backend installed separately. Default visa_library="" uses installed VISA;
use "@py" only when deliberately configured with an appropriate backend.

## Public API

- Construct KeithleyPhotocurrent(resource, expected_model=..., max_abs_bias_v=...,
  max_current_a=..., channel="a"). No I/O on construction or import.
- connect(), or use `with KeithleyPhotocurrent(...) as smu:`.
- configure(Settings(bias_v=..., current_limit_a=..., nplc=1,
  current_range_a=None, remote_sense=False)). Output remains off.
- `with smu.output_enabled():` then `smu.read()` for one instrument reading.
- For settling/averaging, import `read_point` from the routine module and call
  `read_point(smu, samples=3, settle_s=0.1)` inside that output context.
- close() confirms selected-channel output off on a healthy connection.
- The routine `scan_map(...)` enables output for the scan, moves, settles, reads
  and streams CSV. Optional on_pixel and acquisition_metadata support the GUI.

Choose the exact instrument model, channel, address and DUT limits yourself.
No default sample-safe voltage/current can be inferred from a manual.
This first wrapper deliberately supports at most 20 V and 0.1 A, in addition to
stricter user limits. These are software scope caps, NOT safe DUT ratings.
Current compliance minimum: 10 nA for 260x/261x, 100 pA for 263x.
Model-specific operating envelopes and current-limit accuracy still apply.

## Acquisition semantics

This is a STEP-AND-SETTLE map, not a continuous/fly scan. Hold fixed bias across
pixels; move_and_wait(x,y) must block until both axes arrive and return measured
coordinates. The callback owns travel limits, motion timeout, position tolerance,
coordinate calibration and cancellation while moving. A returned command position
must not be presented as a measured position. Units are caller-defined and recorded.
The wrapper never moves hardware or controls the laser by itself.

Use bias_v=0 for a nominal short-circuit photocurrent measurement, or choose an
explicit nonzero bias appropriate for your sample. Even at 0 V, OUTPUT_ON is needed
for the intended source-voltage/measure-current mode. High-Z off is not 0 V clamp.

read_point returns signed current and measured voltage, raw readings, selected
current range and host start/end times. Mean and sample standard deviation are
computed per pixel; a single sample has no sample standard deviation (None).
Compliance, overflow, malformed data and output-off readings reject the pixel and
fault the session. Compliance is a post-reading state snapshot, not a latched
history of every transient during integration. Timestamps are HOST times, not
instrument-buffer timestamps or exact exposure/trigger times.

At 50 Hz, 1 NPLC corresponds to 20 ms ADC aperture. Three readings are not a
60 ms guaranteed pixel dwell: voltage/current conversion, autozero, range-dependent
delay, communication, motion and explicit settling add time. Measure settling on
your actual sample/cabling; 0.1 s in the example is illustrative, not validated.
Autozero AUTO and automatic measurement delay are retained. Digital filtering and
relative offsets are explicitly disabled. On 263x, the low-range ~1 Hz analog
filter is explicitly disabled to avoid unintended pixel mixing. This trades noise
for response speed; validate low-current noise and settling before acquisition.
High-capacitance mode is disabled, so capacitive DUT operation needs separate review.
Use a fixed current range once the signal is characterised if timing consistency
matters; an out-of-range value is rejected rather than silently mapped.

## Light versus dark

A map acquired with the laser on contains total illuminated current, not automatic
dark-subtracted photocurrent. Acquire a dark map with the SAME bias, coordinates,
settings and settling, then compute I_photo(x,y)=I_light(x,y)-I_dark(x,y).
Preserve both raw maps and subtraction provenance. A single dark baseline is only
appropriate after checking that spatial/time-dependent dark variation is negligible.
Shutter/laser control and dark-map subtraction are deliberately not invented here.

## Data and failure handling

CSV is opened with exclusive creation; existing files are never overwritten.
The first comment contains JSON metadata. Data rows contain grid indices, requested
and actual coordinates, mean current/voltage, standard deviation and raw readings
as JSON. Rows are flushed after each pixel. Read with pandas.read_csv(path, comment="#").
The final comment says complete only after all pixels AND confirmed output disable.
Exceptions retain partial data and add a partial footer where writing is possible.
Missing complete footer means incomplete/unconfirmed. Disk failure can prevent a
footer; flush is not a power-loss durability guarantee.

Cancellation is checked before motion, during host settling and between reads.
It cannot interrupt an in-flight VISA query or your blocking movement callback.
VISA timeout bounds individual I/O, not the whole scan or connection establishment.
On a communication fault, output-off is best-effort and explicitly UNCONFIRMED;
no retries or further reads on a potentially desynchronised stream are attempted.
Check the instrument locally and establish a new session before continuing.
Software cleanup cannot guarantee output off after cable loss, process kill or
power/network failure. Use appropriate independent hardware protection/interlocks.

## Ownership and physical setup

Exclusive ownership of the WHOLE instrument is required, even for one channel:
ASCII format and the error queue are global. The wrapper only writes selected SMU
settings and never performs a global reset, clears buffers, clears errors or alters
the other channel. It refuses an existing sweep. It does not detect every possible
external TSP program, so stop other clients/scripts before connecting. Existing
error queue entries cause failure and remain available for local inspection.
Borrowed sessions are not closed, but selected-channel output is still disabled;
termination and format settings are changed and not restored. Instances are single-use.

Make/change connections only with the instrument powered off, following the manual.
An illuminated sample can generate voltage even when the SMU is high impedance.
High-Z does not discharge the DUT or guarantee touch-safe terminals. Use suitable
shielding/low-leakage cabling, appropriate sensing connections and independent
laser safety measures. Do not treat the wrapper as an interlock.

## Sources reviewed

- User-supplied OE-FET/keithley2600 snapshot, commit
  f0434ef683e053d9ebbfa18415ac3ce6dee4e78b, for comparison of driver approach.
- User-supplied 1df479.pdf: Series 2600B System SourceMeter Instrument Reference
  Manual, 2600BS-901-01 Rev. B / May 2013. Relevant printed pages: 7-198 to 7-232,
  especially measure.iv (7-213), NPLC (7-208), compliance (7-219), output (7-228).
- PyVISA official Resources documentation for resource sessions and millisecond timeouts.

## Next bounded task

Follow docs/COMMISSIONING.md for separate device checks before the first small
angular photocurrent map. Preserve the simple device/routine/GUI split.
